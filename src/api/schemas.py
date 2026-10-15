"""Public API contracts. Kept separate from the graph state and the database model on purpose."""

from __future__ import annotations

import uuid
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

IncidentStatus = Literal[
    "queued",
    "processing",
    "failed",
    "needs_review",
    "analysis_ready",
    "awaiting_approval",
    "remediation_rejected",
    "pr_created",
    "pr_skipped_duplicate",
    "pr_failed",
]


class AskTurn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    role: Literal["user", "assistant"]
    content: str = Field(..., min_length=1, max_length=1500)


class AskRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    question: str = Field(..., min_length=3, max_length=500, description="A question about this incident's record")
    history: list[AskTurn] = Field(
        default_factory=list,
        max_length=6,
        description="Earlier turns of the conversation (client-held, untrusted); only used to resolve references",
    )


class AskResponse(BaseModel):
    answer: str
    source: Literal["rules", "model"] = Field(
        description="`rules`: deterministic answer from the record (no LLM); `model`: generated, then validated"
    )
    degraded: bool = Field(description="True when the LLM was unavailable and deterministic guidance was returned")
    answerable: bool = Field(description="False when the incident record does not contain the answer")
    grounded: bool = Field(
        description="False when the model cited no section of the record, or quoted text that is not in it"
    )
    cited_sections: list[str] = Field(description="Sections of the record the answer is based on")
    unverified_quotes: list[str] = Field(description="Quoted spans of the answer not found verbatim in the record")
    flags: list[str] = Field(
        default_factory=list,
        description="Validation flags: `approval_advice` (answer replaced: the assistant never recommends the "
        "decision), `unusable` (no usable model output)",
    )
    follow_ups: list[str] = Field(description="Suggested next questions")
    model: str
    disclaimer: str


class GuidanceStep(BaseModel):
    kind: Literal["action", "check", "wait"]
    audience: str = Field(description="Who this is for: reviewer, operator or developer")
    text: str


class GuidanceCheck(BaseModel):
    name: str
    blocking: bool
    fraction: float
    meaning: str
    advice: str
    detail: str


class GuidanceResponse(BaseModel):
    state: Literal["ok", "attention", "blocked", "waiting"]
    headline: str
    explanation: str
    next_steps: list[GuidanceStep]
    failed_checks: list[GuidanceCheck]
    facts: list[str]
    patch: dict[str, Any] | None = Field(None, description="Counts derived from the stored diff, if one exists")


class AlertOutcome(BaseModel):
    alert: str
    incident_id: str | None
    outcome: Literal["queued", "duplicate", "skipped"]
    detail: str | None = None


class AlertmanagerResult(BaseModel):
    alerts: list[AlertOutcome]
    dropped: int = Field(0, description="Firing alerts beyond the per-notification limit (not processed)")


class IncidentRequest(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={
            "example": {
                "repo_name": "your-org/your-repo",
                "error_message": "TypeError: 'NoneType' object is not subscriptable",
                "stack_trace": 'Traceback (most recent call last):\n  File "/app/src/app.py", line 6, in '
                "load_user\n    name = profile[\"name\"]\nTypeError: 'NoneType' object is not "
                "subscriptable",
            }
        },
    )

    repo_name: str = Field(
        ...,
        min_length=3,
        max_length=201,
        pattern=r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$",
        description="GitHub 'owner/repo'. Must be listed in ALLOWED_REPOSITORIES.",
    )
    error_message: str = Field(..., min_length=1, max_length=20_000)
    stack_trace: str = Field("", max_length=100_000)
    incident_id: uuid.UUID | None = Field(
        None, description="Optional client-generated id. Re-sending the same id is idempotent (no re-analysis)."
    )

    @field_validator("repo_name")
    @classmethod
    def _normalize_repo(cls, value: str) -> str:
        if any(part in {".", ".."} for part in value.split("/")):
            raise ValueError("invalid repository identifier")
        return value.lower()  # GitHub repository names are case-insensitive


class IncidentAccepted(BaseModel):
    incident_id: str
    status: IncidentStatus


class PendingApproval(BaseModel):
    approval_id: str
    action: str
    target_file: str
    patch_sha256: str = Field(description="SHA-256 of suggested_patch; must be echoed back to approve")
    expires_at: str


class RemediationDecision(BaseModel):
    model_config = ConfigDict(extra="forbid")

    approval_id: uuid.UUID
    patch_sha256: str = Field(..., pattern=r"^[0-9a-f]{64}$")
    decision: Literal["approve", "reject"]
    note: str | None = Field(None, max_length=2000)


class IncidentResult(BaseModel):
    incident_id: str
    repo_name: str
    status: IncidentStatus
    status_reason: str | None = None
    error_category: str | None = Field(None, description="Failure / partial-result category (src/errors.py)")
    error_message: str
    submitted_by: str | None = Field(None, description="Authenticated identity that submitted the incident")
    job_attempts: int = Field(0, description="How many times a worker claimed this incident")
    available_at: str | None = Field(None, description="Deferred retry: not picked up by a worker before this time")
    affected_file: str | None = None
    quality_score: float = Field(description="Deterministic rubric score (0-1). Not a probability of correctness.")
    iterations: int = Field(description="Number of completed LLM analysis attempts")
    analysis: dict[str, Any] | None = Field(None, description="Structured RCA plus the evaluator's check breakdown")
    suggested_patch: str | None = None
    pr_url: str | None = None
    pr_number: int | None = None
    pr_branch: str | None = None
    pending_approval: PendingApproval | None = Field(
        None, description="Present while a proposed PR waits for a human decision"
    )
    created_at: str | None = None
    updated_at: str | None = None


class TraceResponse(BaseModel):
    incident_id: str | None = None
    request_id: str | None = Field(None, description="Request that submitted the incident, when recorded")
    status: str | None = None
    error_category: str | None = None
    versions: dict[str, Any] = Field(description="Model, prompt, quality-gate and retrieval versions of the run")
    attempts: list[dict[str, Any]] = Field(description="Per LLM attempt: latency, tokens, gate verdict and decision")
    totals: dict[str, Any] = Field(description="Sums over attempts; estimated_cost_usd is null (no prices configured)")
    final_quality_score: float | None = None


class EvidenceGraphResponse(BaseModel):
    incident_id: str | None = None
    nodes: list[dict[str, Any]] = Field(
        description="incident, claim, evidence (with verification status), patch, files"
    )
    edges: list[dict[str, Any]] = Field(description="from/to/relation: diagnosed_as, supports, suggests, anchors, ...")
    flags: list[str] = Field(description="Missing, unverified or conflicting evidence, stated plainly")
    summary: dict[str, int]


class ErrorBody(BaseModel):
    code: str
    message: str


class ErrorResponse(BaseModel):
    error: ErrorBody


class HealthResponse(BaseModel):
    status: Literal["ok", "ready", "unavailable"]
    checks: dict[str, str] = Field(default_factory=dict)


class IncidentSummary(BaseModel):
    incident_id: str
    repo_name: str
    status: IncidentStatus
    error_category: str | None = None
    quality_score: float
    iterations: int
    affected_file: str | None = None
    submitted_by: str | None = None
    pr_url: str | None = None
    created_at: str | None = None
    updated_at: str | None = None


class IncidentPage(BaseModel):
    items: list[IncidentSummary]
    next_cursor: str | None = Field(None, description="Pass as `cursor` to get the next (older) page")
