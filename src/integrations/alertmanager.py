"""Prometheus Alertmanager webhooks -> OpsPulse incidents.

Reference: https://prometheus.io/docs/alerting/latest/configuration/#webhook_config (payload version "4").
Alertmanager authenticates with the receiver's `http_config.authorization` (Bearer credentials), i.e. an ordinary
OpsPulse API key with the reporter role.

Each *firing* alert becomes one incident; resolved alerts are ignored. The repository comes from an alert label
(`repository` by default, `ALERTMANAGER_REPO_LABEL`); the stack trace from an optional `stack_trace` annotation.
Alerts rarely carry a trace, and without one the analysis will usually report insufficient evidence - which is
the honest result. The incident id is derived from the alert fingerprint and start time, so Alertmanager's
repeated notifications for the same firing alert are idempotent.
"""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass
from typing import Any

from src.integrations.revision import normalize_commit_sha

MAX_BODY_BYTES = 1_000_000
MAX_ALERTS = 20  # per notification; Alertmanager groups alerts, a larger group is truncated (and reported)
_NAMESPACE = uuid.UUID("2f6d9c1a-7b3e-4e5f-8a90-1c2d3e4f5a6b")
_REPO_RE = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")


class AlertmanagerPayloadError(ValueError):
    """The body is not an Alertmanager webhook notification."""


@dataclass(frozen=True)
class AlertIncident:
    incident_id: uuid.UUID
    alert_name: str
    repo_name: str | None  # None when the label is missing or malformed
    error_message: str
    stack_trace: str
    commit_sha: str | None = None


def _text(value: Any, limit: int) -> str:
    return value.strip()[:limit] if isinstance(value, str) else ""


def to_incidents(payload: dict[str, Any], repo_label: str) -> tuple[list[AlertIncident], int]:
    """Return (incidents for firing alerts, number of firing alerts dropped by MAX_ALERTS)."""
    alerts = payload.get("alerts")
    if payload.get("version") != "4" or not isinstance(alerts, list):
        raise AlertmanagerPayloadError("expected an Alertmanager webhook payload (version 4)")
    firing = [a for a in alerts if isinstance(a, dict) and a.get("status") == "firing"]
    incidents = []
    for alert in firing[:MAX_ALERTS]:
        labels = alert["labels"] if isinstance(alert.get("labels"), dict) else {}
        annotations = alert["annotations"] if isinstance(alert.get("annotations"), dict) else {}
        name = _text(labels.get("alertname"), 200) or "UnnamedAlert"
        summary = _text(annotations.get("summary"), 2000)
        description = _text(annotations.get("description"), 15_000)
        message = "\n".join(part for part in (f"{name}: {summary}" if summary else name, description) if part)
        repo = _text(labels.get(repo_label), 201).lower()
        key = f"alertmanager:{_text(alert.get('fingerprint'), 64)}:{_text(alert.get('startsAt'), 64)}"
        if key == "alertmanager::":  # no identity at all: fall back to the content
            key = f"alertmanager:{name}:{repo}:{message}"
        incidents.append(
            AlertIncident(
                incident_id=uuid.uuid5(_NAMESPACE, key),
                alert_name=name,
                repo_name=repo if _REPO_RE.match(repo) else None,
                error_message=message[:20_000],
                stack_trace=_text(annotations.get("stack_trace"), 100_000),
                commit_sha=normalize_commit_sha(labels.get("commit_sha") or annotations.get("commit_sha")),
            )
        )
    return incidents, max(len(firing) - MAX_ALERTS, 0)
