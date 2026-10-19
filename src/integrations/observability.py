"""Optional Langfuse tracing (Python SDK v4, OpenTelemetry based). A no-op when keys are not configured.

Privacy default: prompts and model outputs contain stack traces and private source code, so unless
LANGFUSE_CAPTURE_CONTENT=true every string longer than `_MAX_TRACE_STRING` is replaced by a length marker
before it leaves the process. Short values (incident id, model name, scores, node names) are kept.
"""

from __future__ import annotations

import logging
import sys
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from functools import lru_cache
from typing import Any, Literal

from langchain_core.runnables import RunnableConfig
from langfuse import Langfuse, propagate_attributes

from src.config import settings
from src.logging_config import redact
from src.versions import PROMPT_VERSION, run_metadata

logger = logging.getLogger(__name__)

_MAX_TRACE_STRING = 200


def mask_trace_data(*, data: Any, **_: Any) -> Any:
    """Langfuse `mask` callback: redact secrets everywhere, drop long free-text unless capture is enabled."""
    if isinstance(data, str):
        if not settings.langfuse_capture_content and len(data) > _MAX_TRACE_STRING:
            return f"[content omitted: {len(data)} chars]"
        return redact(data)
    if isinstance(data, dict):
        return {k: mask_trace_data(data=v) for k, v in data.items()}
    if isinstance(data, (list, tuple)):
        return [mask_trace_data(data=v) for v in data]
    return data


@lru_cache(maxsize=1)
def _client() -> Langfuse | None:
    """Process-wide Langfuse client with masking, or None when disabled / not constructible."""
    if not settings.langfuse_enabled:
        logger.info("Langfuse keys not set - tracing disabled")
        return None
    try:
        return Langfuse(
            public_key=settings.langfuse_public_key,
            secret_key=settings.langfuse_secret_key,
            host=settings.langfuse_host,
            mask=mask_trace_data,
        )
    except (ValueError, TypeError, OSError, RuntimeError) as exc:
        logger.warning("Langfuse client could not be created (%s) - tracing disabled", type(exc).__name__)
        return None


class Observation:
    """Thin wrapper so callers never need to know whether tracing is enabled."""

    def __init__(self, inner: Any | None = None) -> None:
        self._inner = inner

    def update(self, *, output: Any = None, metadata: dict[str, Any] | None = None, error: str | None = None) -> None:
        if self._inner is None:
            return
        kwargs: dict[str, Any] = {"output": output, "metadata": metadata}
        if error:
            kwargs.update(level="ERROR", status_message=error)
        try:
            self._inner.update(**{k: v for k, v in kwargs.items() if v is not None})
        except Exception as exc:  # noqa: BLE001 - telemetry must never break incident processing
            logger.warning("Langfuse update failed: %s", type(exc).__name__)


ObservationType = Literal["span", "chain", "retriever", "evaluator", "tool"]


@contextmanager
def _guarded(*factories: Callable[[], Any]) -> Iterator[list[Any]]:
    """Enter telemetry context managers so that telemetry can never break the work it observes.

    A failure while *starting* (or later *ending*) a span is logged and ignored; the wrapped code then runs without
    that span. An exception raised by the wrapped code itself propagates unchanged and is never suppressed.
    """
    entered: list[Any] = []
    values: list[Any] = []
    try:
        for factory in factories:
            manager = factory()
            values.append(manager.__enter__())
            entered.append(manager)
    except Exception as exc:  # noqa: BLE001 - see the docstring
        logger.warning("Langfuse span could not be started (%s) - continuing without it", type(exc).__name__)
        _leave(entered, (None, None, None))
        yield [None] * len(factories)
        return
    exc_info: tuple[Any, Any, Any] = (None, None, None)
    try:
        yield values
    except BaseException:
        exc_info = sys.exc_info()
        raise
    finally:
        _leave(entered, exc_info)


def _leave(entered: list[Any], exc_info: tuple[Any, Any, Any]) -> None:
    for manager in reversed(entered):
        try:
            manager.__exit__(*exc_info)  # the return value is ignored on purpose: never swallow the caller's error
        except Exception as exc:  # noqa: BLE001 - telemetry must never break incident processing
            logger.warning("Langfuse span could not be closed (%s)", type(exc).__name__)


@contextmanager
def observe(
    name: str, as_type: ObservationType = "span", metadata: dict[str, Any] | None = None
) -> Iterator[Observation]:
    """Child observation of the current trace (no-op when tracing is disabled or failing).

    Exceptions raised by the wrapped code propagate unchanged; Langfuse records them on the span.
    """
    client = _client()
    if client is None:
        yield Observation()
        return
    with _guarded(lambda: client.start_as_current_observation(name=name, as_type=as_type, metadata=metadata)) as (
        inner,
    ):
        yield Observation(inner)


@contextmanager
def incident_trace(incident_id: str, repo_name: str, fingerprint: str) -> Iterator[Observation]:
    """Root observation for one incident; session/trace attributes propagate to every child (incl. LLM calls)."""
    client = _client()
    if client is None:
        yield Observation()
        return
    attributes = {
        k: str(v)[:200] for k, v in {**run_metadata(), "repo_name": repo_name, "fingerprint": fingerprint[:16]}.items()
    }
    with _guarded(
        lambda: propagate_attributes(
            session_id=incident_id,
            trace_name="opspulse.incident",
            tags=["opspulse"],
            version=PROMPT_VERSION,
            metadata=attributes,
        ),
        lambda: client.start_as_current_observation(name="incident.process", as_type="chain"),
    ) as (_, inner):
        yield Observation(inner)


def build_run_config(incident_id: str, repo_name: str, fingerprint: str) -> RunnableConfig:
    """RunnableConfig for graph.invoke(): Langfuse callback (if enabled) + correlation metadata."""
    config: RunnableConfig = {
        "run_name": "opspulse_incident_rca",
        "tags": ["opspulse"],
        "metadata": {
            "incident_id": incident_id,
            "repo_name": repo_name,
            "fingerprint": fingerprint,
            "model": settings.model_name,
            "prompt_version": PROMPT_VERSION,
        },
        # Backstop only: the router already bounds the loop (see src/agent/graph.py).
        "recursion_limit": 6 + 2 * settings.max_analysis_iterations,
    }
    handler_class = _callback_handler_class() if _client() is not None else None
    if handler_class is not None:
        # The handler attaches LangGraph node spans and the LLM generation (model, latency, token usage)
        # to the current trace.
        config["callbacks"] = [handler_class(public_key=settings.langfuse_public_key)]
    return config


@lru_cache(maxsize=1)
def _callback_handler_class() -> type | None:
    """Langfuse's LangChain handler, imported lazily: a missing optional integration disables LLM-level
    tracing instead of preventing the application from starting."""
    try:
        from langfuse.langchain import CallbackHandler
    except ImportError as exc:
        logger.warning("Langfuse LangChain integration unavailable (%s) - LLM generations not traced", exc)
        return None
    return CallbackHandler


def flush_traces() -> None:
    """Flush buffered spans (end of request / shutdown). Failures are logged, never raised."""
    client = _client()
    if client is None:
        return
    try:
        client.flush()
    except Exception as exc:  # noqa: BLE001 - telemetry must never break incident processing
        logger.warning("Langfuse flush failed: %s", type(exc).__name__)
