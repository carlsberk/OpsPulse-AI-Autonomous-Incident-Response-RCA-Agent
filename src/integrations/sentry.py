"""Sentry Integration Platform webhooks -> OpsPulse incidents.

Reference: https://docs.sentry.io/organization/integrations/integration-platform/webhooks/
- Requests carry `Sentry-Hook-Resource` and `Sentry-Hook-Signature` = hex HMAC-SHA256 of the body, keyed with the
  integration's Client Secret. Sentry's sample verifies `JSON.stringify(body)`, i.e. a compact re-serialisation
  rather than the raw bytes, so both forms are accepted (each with a constant-time comparison).
- Issue-alert payloads (`event_alert`) and error payloads (`error`) contain an event with
  `exception.values[].{type,value,stacktrace.frames[]}`; frames are ordered oldest first.

Everything in the payload is untrusted: it is size-limited, reduced to an error message and a synthetic
Python-style traceback, and then goes through the normal pipeline like any other incident.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import uuid
from dataclasses import dataclass
from typing import Any

from src.integrations.revision import normalize_commit_sha

SUPPORTED_RESOURCES = frozenset({"event_alert", "error"})
MAX_BODY_BYTES = 1_000_000
MAX_FRAMES = 60
_NAMESPACE = uuid.UUID("6c0f1b5e-3f4a-4c8e-9a41-0d5b9a7e2c11")  # stable ids for repeated deliveries


class SentryPayloadError(ValueError):
    """The payload is not an event we can turn into an incident."""


@dataclass(frozen=True)
class SentryIncident:
    incident_id: uuid.UUID
    project_id: str
    error_message: str
    stack_trace: str
    commit_sha: str | None = None


def signature_is_valid(secret: str, raw_body: bytes, provided: str | None) -> bool:
    if not secret or not provided:
        return False
    candidates = [raw_body]
    try:
        parsed = json.loads(raw_body)
    except ValueError:
        parsed = None  # not JSON: only the raw-body form can match; the payload is rejected later anyway
    if parsed is not None:
        candidates.append(json.dumps(parsed, separators=(",", ":"), ensure_ascii=False).encode("utf-8"))
    valid = False
    for body in candidates:  # evaluate every candidate: no early exit
        digest = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
        valid |= hmac.compare_digest(digest, provided.strip().lower())
    return valid


def parse_project_map(raw: str) -> dict[str, str]:
    """SENTRY_PROJECT_REPOS="<project id>:<owner/repo>,..." -> {project id: repo}."""
    mapping = {}
    for entry in (item.strip() for item in raw.split(",")):
        if not entry:
            continue
        project, _, repo = entry.partition(":")
        if not project.strip() or "/" not in repo:
            raise ValueError("SENTRY_PROJECT_REPOS entries must look like <project id>:<owner/repo>")
        mapping[project.strip()] = repo.strip().lower()
    return mapping


def _frame_line(frame: dict[str, Any]) -> list[str]:
    path = str(frame.get("abs_path") or frame.get("filename") or "<unknown>")[:300]
    line = frame.get("lineno")
    function = str(frame.get("function") or "<unknown>")[:120]
    rendered = [f'  File "{path}", line {line if isinstance(line, int) else 0}, in {function}']
    context = frame.get("context_line")
    if isinstance(context, str) and context.strip():
        rendered.append(f"    {context.strip()[:300]}")
    return rendered


def to_incident(payload: dict[str, Any]) -> SentryIncident:
    data: dict[str, Any] = payload["data"] if isinstance(payload.get("data"), dict) else {}
    event: Any = data.get("event") or data.get("error")
    if not isinstance(event, dict):
        raise SentryPayloadError("payload has no event")
    event_id = str(event.get("event_id") or "")
    if not event_id:
        raise SentryPayloadError("event has no event_id")
    exception: dict[str, Any] = event["exception"] if isinstance(event.get("exception"), dict) else {}
    raw_values = exception.get("values")
    values = [v for v in raw_values if isinstance(v, dict)] if isinstance(raw_values, list) else []
    last: dict[str, Any] = values[-1] if values else {}
    exc_type = str(last.get("type") or "").strip()
    exc_value = str(last.get("value") or "").strip()
    message = f"{exc_type}: {exc_value}" if exc_type else str(event.get("title") or event.get("message") or "")
    if not message.strip():
        raise SentryPayloadError("event has neither an exception nor a title")

    stacktrace = last.get("stacktrace")
    raw_frames = stacktrace.get("frames") if isinstance(stacktrace, dict) else None
    frames = [f for f in raw_frames if isinstance(f, dict)] if isinstance(raw_frames, list) else []
    if any(f.get("in_app") is True for f in frames):
        frames = [f for f in frames if f.get("in_app") is not False]  # keep application frames
    lines = ["Traceback (most recent call last):"] if frames else []
    for frame in frames[-MAX_FRAMES:]:
        lines += _frame_line(frame)
    if frames:
        lines.append(message)

    return SentryIncident(
        incident_id=uuid.uuid5(_NAMESPACE, f"sentry:{event_id}"),
        project_id=str(event.get("project") or ""),
        error_message=message[:20_000],
        stack_trace="\n".join(lines)[:100_000],
        commit_sha=normalize_commit_sha(event.get("release")),
    )
