"""Review timeline of one incident: who submitted, what was proposed, who decided what and why, what happened next.

Derived from rows the system already keeps (the incident and every remediation approval, including superseded and
expired ones), so it needs no extra write path that could drift from the real state. It is a *report*, not a
tamper-evident log: rows are updated in place by the workflow. Every event carries only facts present in the record;
nothing is inferred.
"""

from __future__ import annotations

from typing import Any

_DECISION_EVENT = {
    "approved": "approved",
    "executed": "approved",
    "execution_failed": "approved",
    "rejected": "rejected",
    "expired": "expired",
    "superseded": "superseded",
}


def build_audit(incident: dict[str, Any], approvals: list[dict[str, Any]]) -> dict[str, Any]:
    analysis = incident.get("analysis") or {}
    meta = analysis.get("execution_metadata") or {}
    events: list[dict[str, Any]] = [
        {
            "at": incident.get("created_at"),
            "event": "submitted",
            "actor": incident.get("submitted_by"),
            "detail": f"incident reported for {incident.get('repo_name')}",
        }
    ]
    if analysis:
        events.append(
            {
                "at": incident.get("updated_at") if not approvals else approvals[0].get("created_at"),
                "event": "analysed",
                "actor": "agent",
                "detail": (
                    f"gate score {incident.get('quality_score')}, {incident.get('iterations')} attempt(s); "
                    f"model {meta.get('model')}, prompt {meta.get('prompt_version')}, "
                    f"gate {meta.get('evaluator_version')}"
                ),
            }
        )
    for approval in approvals:
        events.append(
            {
                "at": approval.get("created_at"),
                "event": "proposed",
                "actor": "agent",
                "detail": f"open a draft PR changing {approval.get('target_file')}",
                "patch_sha256": approval.get("patch_sha256"),
                "expires_at": approval.get("expires_at"),
            }
        )
        status = str(approval.get("status"))
        if status in _DECISION_EVENT:
            events.append(
                {
                    "at": approval.get("decided_at"),
                    "event": _DECISION_EVENT[status],
                    "actor": approval.get("reviewer") or ("system" if status in ("expired", "superseded") else None),
                    "detail": approval.get("decision_note") or "",
                    "patch_sha256": approval.get("patch_sha256"),
                }
            )
        if status == "executed" and incident.get("pr_url"):
            events.append(
                {
                    "at": approval.get("decided_at"),
                    "event": "pr_opened",
                    "actor": "system",
                    "detail": f"draft PR on branch {incident.get('pr_branch')}: {incident.get('pr_url')}",
                }
            )
        if status == "execution_failed":
            events.append(
                {
                    "at": approval.get("decided_at"),
                    "event": "pr_failed",
                    "actor": "system",
                    "detail": str(incident.get("status_reason") or "pull request could not be opened"),
                }
            )
    events.sort(key=lambda e: str(e.get("at") or ""))
    return {"incident_id": incident.get("incident_id"), "events": events, "merge_policy": "never automatic"}
