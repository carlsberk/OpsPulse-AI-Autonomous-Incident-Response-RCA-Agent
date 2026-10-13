"""Groq chat-model boundary: construction, JSON mode, bounded retries and error classification."""

from __future__ import annotations

import json
import logging
import re
import time
from collections.abc import Callable
from dataclasses import dataclass
from functools import lru_cache
from typing import Any

import groq
from langchain_core.messages import BaseMessage
from langchain_core.runnables import Runnable, RunnableConfig
from pydantic import SecretStr

from src.config import settings

logger = logging.getLogger(__name__)


class LLMError(RuntimeError):
    """Unrecoverable provider failure. `category` is safe to persist and return to API clients."""

    def __init__(self, category: str, message: str) -> None:
        super().__init__(message)
        self.category = category


@lru_cache(maxsize=4)
def get_chat_model(temperature: float) -> Runnable[Any, Any]:
    """ChatGroq in JSON mode. The SDK retries transient errors at most `LLM_MAX_RETRIES` times."""
    if not settings.groq_api_key:
        raise LLMError("not_configured", "GROQ_API_KEY is not set")
    from langchain_groq import ChatGroq

    model = ChatGroq(
        model=settings.model_name,
        api_key=SecretStr(settings.groq_api_key),
        temperature=temperature,
        max_tokens=settings.llm_max_output_tokens,
        max_retries=settings.llm_max_retries,
        timeout=settings.llm_timeout_seconds,
    )
    return model.bind(response_format={"type": "json_object"})


def _error_code(exc: groq.APIStatusError) -> str | None:
    """Groq error bodies look like {"error": {"code": "...", "message": "..."}}."""
    body = exc.body if isinstance(exc.body, dict) else {}
    error = body.get("error", body)
    code = error.get("code") if isinstance(error, dict) else None
    return code if isinstance(code, str) else None


_LIMIT_KIND = re.compile(r"\((TPM|TPD|RPM|RPD|OTPM|ASH|ASD)\)")


def _limit_kind(exc: groq.APIStatusError) -> str:
    """Which provider limit was hit, e.g. "TPD" (tokens per day). Only this label is kept from the message,
    which also names the organisation."""
    body = exc.body if isinstance(exc.body, dict) else {}
    error = body.get("error", body)
    message = error.get("message") if isinstance(error, dict) else None
    match = _LIMIT_KIND.search(message) if isinstance(message, str) else None
    return f" ({match.group(1)})" if match else ""


def _is_request_too_large(exc: groq.APIStatusError) -> bool:
    """Groq answers 429 both for "slow down" and for a single request larger than a per-minute token cap
    (e.g. a 1000 output-tokens-per-minute free-tier limit). Only the first one is worth retrying later."""
    body = exc.body if isinstance(exc.body, dict) else {}
    error = body.get("error", body)
    message = error.get("message") if isinstance(error, dict) else None
    return isinstance(message, str) and message.startswith("Request too large")


@dataclass(frozen=True)
class LLMReply:
    text: str
    latency_ms: int
    input_tokens: int | None = None  # as reported by the provider; None when not reported
    output_tokens: int | None = None
    truncated: bool = False  # the reply stopped at the output-token limit (finish_reason == "length")


ModelFactory = Callable[[float], Runnable[Any, Any]]  # temperature -> chat model


def invoke_json_model(
    messages: list[BaseMessage],
    temperature: float,
    config: RunnableConfig | None,
    model_factory: ModelFactory | None = None,
) -> LLMReply:
    """Call the model once (plus the SDK's bounded transport retries).

    Returns an empty-text reply when Groq rejected the generation because it was not valid JSON (that counts as a failed
    attempt the evaluator can give feedback on). Raises `LLMError` for everything a retry of the
    analysis cannot fix: auth, rate limits, timeouts, unknown model, connectivity.
    """
    started = time.perf_counter()
    try:
        response = (model_factory or get_chat_model)(temperature).invoke(messages, config=config)
    except groq.BadRequestError as exc:
        code = _error_code(exc)
        if code == "json_validate_failed":
            logger.warning("Groq rejected a non-JSON generation")
            return LLMReply("", _elapsed_ms(started))
        if code in {"model_decommissioned", "model_not_found"}:
            raise LLMError("model_unavailable", f"model {settings.model_name!r} is unavailable") from exc
        raise LLMError("bad_request", "the LLM provider rejected the request") from exc
    except groq.AuthenticationError as exc:
        raise LLMError("auth", "the LLM provider rejected the API key") from exc
    except groq.PermissionDeniedError as exc:
        raise LLMError("auth", "the API key is not allowed to use this model") from exc
    except groq.NotFoundError as exc:
        raise LLMError("model_unavailable", f"model {settings.model_name!r} is unavailable") from exc
    except groq.RateLimitError as exc:
        if _is_request_too_large(exc):
            raise LLMError(
                "request_too_large", f"one request exceeds the provider's per-minute token limit{_limit_kind(exc)}"
            ) from exc
        raise LLMError("rate_limited", f"the LLM provider rate limit was exceeded{_limit_kind(exc)}") from exc
    except groq.APITimeoutError as exc:
        raise LLMError("timeout", "the LLM provider timed out") from exc
    except groq.APIConnectionError as exc:
        raise LLMError("connection", "could not reach the LLM provider") from exc
    except groq.APIStatusError as exc:
        raise LLMError("provider_error", f"the LLM provider returned HTTP {exc.status_code}") from exc
    content: Any = response.content
    usage = getattr(response, "usage_metadata", None) or {}
    return LLMReply(
        text=content if isinstance(content, str) else json.dumps(content),
        latency_ms=_elapsed_ms(started),
        input_tokens=usage.get("input_tokens"),
        output_tokens=usage.get("output_tokens"),
        truncated=(getattr(response, "response_metadata", None) or {}).get("finish_reason") == "length",
    )


def _elapsed_ms(started: float) -> int:
    return int((time.perf_counter() - started) * 1000)
