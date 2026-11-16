import groq
import httpx
import pytest

from src.integrations import llm
from src.integrations.llm import LLMError, invoke_json_model

_REQUEST = httpx.Request("POST", "https://api.groq.test/openai/v1/chat/completions")


def _status_error(cls, status, code=None, message="x"):
    body = {"error": {"code": code, "message": message}} if code else None
    return cls("provider said no", response=httpx.Response(status, request=_REQUEST), body=body)


class Raising:
    def __init__(self, exc):
        self.exc = exc

    def invoke(self, messages, config=None):
        raise self.exc


@pytest.mark.parametrize(
    "exc,category",
    [
        (_status_error(groq.RateLimitError, 429), "rate_limited"),
        (
            _status_error(groq.RateLimitError, 429, "rate_limit_exceeded", "Rate limit reached for model"),
            "rate_limited",
        ),
        (
            _status_error(groq.RateLimitError, 429, "rate_limit_exceeded", "Request too large for model `m` on OTPM"),
            "request_too_large",
        ),
        (_status_error(groq.AuthenticationError, 401), "auth"),
        (_status_error(groq.NotFoundError, 404), "model_unavailable"),
        (_status_error(groq.BadRequestError, 400, "model_decommissioned"), "model_unavailable"),
        (_status_error(groq.BadRequestError, 400, "context_length_exceeded"), "bad_request"),
        (_status_error(groq.InternalServerError, 503), "provider_error"),
        (groq.APITimeoutError(request=_REQUEST), "timeout"),
        (groq.APIConnectionError(request=_REQUEST), "connection"),
    ],
)
def test_provider_errors_are_classified(monkeypatch, exc, category):
    monkeypatch.setattr(llm, "get_chat_model", lambda temperature: Raising(exc))
    with pytest.raises(LLMError) as info:
        invoke_json_model([], 0.0, None)
    assert info.value.category == category


def test_json_validation_failure_is_a_failed_attempt_not_an_outage(monkeypatch):
    exc = _status_error(groq.BadRequestError, 400, "json_validate_failed")
    monkeypatch.setattr(llm, "get_chat_model", lambda temperature: Raising(exc))
    assert invoke_json_model([], 0.0, None).text == ""


def test_missing_api_key_is_reported_as_not_configured():
    llm.get_chat_model.cache_clear()
    with pytest.raises(LLMError) as info:
        llm.get_chat_model(0.0)
    assert info.value.category == "not_configured"


class Replying:
    def __init__(self, message):
        self.message = message

    def invoke(self, messages, config=None):
        return self.message


def test_reply_cut_off_at_the_token_limit_is_flagged(monkeypatch):
    from langchain_core.messages import AIMessage

    cut = AIMessage(content='{"incident_summary": {', response_metadata={"finish_reason": "length"})
    monkeypatch.setattr(llm, "get_chat_model", lambda temperature: Replying(cut))
    assert invoke_json_model([], 0.0, None).truncated is True
    done = AIMessage(content="{}", response_metadata={"finish_reason": "stop"})
    monkeypatch.setattr(llm, "get_chat_model", lambda temperature: Replying(done))
    assert invoke_json_model([], 0.0, None).truncated is False


def test_rate_limit_message_keeps_only_the_limit_kind(monkeypatch):
    message = "Rate limit reached for model `m` in organization `org_secret` on tokens per day (TPD): Limit 200000"
    exc = _status_error(groq.RateLimitError, 429, "rate_limit_exceeded", message)
    monkeypatch.setattr(llm, "get_chat_model", lambda temperature: Raising(exc))
    with pytest.raises(LLMError) as info:
        invoke_json_model([], 0.0, None)
    assert str(info.value) == "the LLM provider rate limit was exceeded (TPD)"


def test_the_chat_model_is_built_in_json_mode_with_the_configured_limits(set_settings):
    set_settings(groq_api_key="test-key-not-real", llm_max_retries=1, llm_timeout_seconds=7)
    llm.get_chat_model.cache_clear()
    try:
        model = llm.get_chat_model(0.0)  # constructing the client makes no network call
        assert model.kwargs == {"response_format": {"type": "json_object"}}  # type: ignore[attr-defined]
        assert model.bound.max_retries == 1 and model.bound.request_timeout == 7  # type: ignore[attr-defined]
        assert llm.get_chat_model(0.0) is model  # cached per temperature
    finally:
        llm.get_chat_model.cache_clear()
