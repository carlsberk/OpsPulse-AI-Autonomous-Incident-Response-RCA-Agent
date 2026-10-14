"""Structured JSON logging with incident-id correlation and secret redaction."""

from __future__ import annotations

import json
import logging
import re
from contextvars import ContextVar
from datetime import UTC, datetime

incident_id_var: ContextVar[str | None] = ContextVar("incident_id", default=None)
request_id_var: ContextVar[str | None] = ContextVar("request_id", default=None)

_SECRET_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"\b(gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,})"), "[REDACTED_GITHUB_TOKEN]"),
    (re.compile(r"\bgsk_[A-Za-z0-9]{20,}"), "[REDACTED_GROQ_KEY]"),
    (re.compile(r"\b[sp]k-lf-[A-Za-z0-9-]{8,}"), "[REDACTED_LANGFUSE_KEY]"),
    (re.compile(r"(?i)\b(bearer|token)\s+[A-Za-z0-9._~+/=-]{16,}"), r"\1 [REDACTED]"),
    # user:password@ in connection strings -> user:[REDACTED]@
    (re.compile(r"(\b[a-z][a-z0-9+.-]*://[^:/\s@]+:)[^@\s]+@"), r"\1[REDACTED]@"),
)


def redact(text: str) -> str:
    """Mask credential-shaped substrings. Best effort: it reduces, not eliminates, leak risk."""
    for pattern, replacement in _SECRET_PATTERNS:
        text = pattern.sub(replacement, text)
    return text


class JsonFormatter(logging.Formatter):
    """One JSON object per line; every message and traceback passes through `redact`."""

    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "ts": datetime.fromtimestamp(record.created, tz=UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": redact(record.getMessage()),
        }
        incident_id = incident_id_var.get()
        if incident_id:
            payload["incident_id"] = incident_id
        request_id = request_id_var.get()
        if request_id:
            payload["request_id"] = request_id
        if record.exc_info:
            payload["exception"] = redact(self.formatException(record.exc_info))
        return json.dumps(payload, ensure_ascii=False)


def configure_logging(level: str = "INFO") -> None:
    """Install the JSON formatter on the root logger (idempotent)."""
    root = logging.getLogger()
    if any(isinstance(h.formatter, JsonFormatter) for h in root.handlers):
        return
    handler = logging.StreamHandler()
    handler.setFormatter(JsonFormatter())
    root.handlers = [handler]
    root.setLevel(level)
    # Third-party HTTP clients log full URLs at DEBUG/INFO; keep them quiet by default.
    for noisy in ("httpx", "httpcore", "urllib3", "github"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
