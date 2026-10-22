"""HTTP endpoints."""

from __future__ import annotations

import logging
import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import JSONResponse, Response
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest
from sqlalchemy.exc import SQLAlchemyError

from src.api.auth import Principal, authenticate, rate_limited_asker, rate_limited_reporter, require_role
from src.api.schemas import (
    AskRequest,
    AskResponse,
    AuditResponse,
    ErrorResponse,
    EvidenceGraphResponse,
    GuidanceResponse,
    HealthResponse,
    IncidentAccepted,
    IncidentPage,
    IncidentRequest,
    IncidentResult,
    IncidentStatus,
    IncidentSummary,
    RemediationDecision,
    TraceResponse,
)
from src.config import settings
from src.db.client import ping_database
from src.db.repositories import InvalidCursorError
from src.services import ask_service, evidence_graph, guidance, incident_service, trace
from src.services.incident_service import DatabaseUnavailableError, IncidentConflictError, IncidentNotFoundError
from src.services.remediation_service import ApprovalError

logger = logging.getLogger(__name__)
router = APIRouter()

_ERRORS: dict[int | str, dict[str, Any]] = {
    401: {"model": ErrorResponse, "description": "Missing or invalid API key"},
    403: {"model": ErrorResponse, "description": "Role not allowed, repository not allowed, or self-approval"},
    503: {"model": ErrorResponse, "description": "Database unavailable or authentication not configured"},
}


@router.get("/healthz", response_model=HealthResponse, tags=["health"])
def healthz() -> HealthResponse:
    """Liveness: the process is up. Does not touch external services."""
    return HealthResponse(status="ok")


@router.get("/readyz", response_model=HealthResponse, tags=["health"], responses={503: {"model": HealthResponse}})
def readyz() -> HealthResponse | JSONResponse:
    """Readiness: the database answers. Reports only ok/unavailable, never connection details."""
    try:
        ping_database()
    except SQLAlchemyError:
        body = HealthResponse(status="unavailable", checks={"database": "unavailable"})
        return JSONResponse(status_code=503, content=body.model_dump())
    return HealthResponse(status="ready", checks={"database": "ok"})


@router.get("/metrics", tags=["health"], response_class=Response, include_in_schema=True)
def prometheus_metrics() -> Response:
    """Prometheus metrics: counters/histograms with status and outcome labels only (no content, no identities)."""
    if not settings.metrics_enabled:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "metrics disabled")
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)


@router.get(
    "/incidents",
    response_model=IncidentPage,
    dependencies=[Depends(authenticate)],
    tags=["incidents"],
    responses={**_ERRORS, 400: {"model": ErrorResponse}},
)
def list_incidents(
    status_filter: Annotated[IncidentStatus | None, Query(alias="status")] = None,
    repo_name: Annotated[str | None, Query(max_length=201)] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    cursor: Annotated[str | None, Query(max_length=200)] = None,
) -> IncidentPage:
    """Newest first. Filter e.g. `?status=awaiting_approval` to find proposals waiting for review."""
    try:
        items, next_cursor = incident_service.list_incidents(
            status_filter, repo_name.lower() if repo_name else None, limit, cursor
        )
    except InvalidCursorError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "invalid cursor") from exc
    except DatabaseUnavailableError as exc:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "database unavailable") from exc
    return IncidentPage(items=[IncidentSummary(**i) for i in items], next_cursor=next_cursor)


@router.post(
    "/webhook/incident",
    response_model=IncidentAccepted | IncidentResult,
    status_code=status.HTTP_202_ACCEPTED,
    tags=["incidents"],
    responses={
        **_ERRORS,
        200: {
            "model": IncidentResult,
            "description": "Synchronous result (wait=true) or existing incident for a repeated incident_id",
        },
        409: {"model": ErrorResponse},
        429: {"model": ErrorResponse, "description": "Per-identity rate limit exceeded (see Retry-After)"},
    },
)
def submit_incident(
    payload: IncidentRequest,
    principal: Annotated[Principal, Depends(rate_limited_reporter)],
    wait: bool = Query(False, description="Run the analysis synchronously and return the full result"),
) -> IncidentAccepted | JSONResponse:
    """Accept an incident report (roles: reporter, reviewer, admin).

    The incident is queued and analysed by a worker (poll `GET /incidents/{id}`), or synchronously with `wait=true`.
    """
    if not settings.is_repository_allowed(payload.repo_name):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "repository is not in ALLOWED_REPOSITORIES")
    incident_id = payload.incident_id or uuid.uuid4()
    try:
        row, created = incident_service.submit_incident(
            incident_id,
            payload.repo_name,
            payload.error_message,
            payload.stack_trace,
            submitted_by=principal.name,
            commit_sha=payload.commit_sha,
        )
    except IncidentConflictError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    except DatabaseUnavailableError as exc:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "database unavailable") from exc

    if not created:
        logger.info("repeated delivery for incident %s ignored", incident_id)
        return JSONResponse(status_code=200, content=IncidentResult(**row).model_dump(mode="json"))
    if wait:
        try:
            claim = incident_service.claim_for_inline_run(incident_id)
            if not claim:  # a worker was faster
                return IncidentAccepted(incident_id=str(incident_id), status="processing")
            result = incident_service.run_incident_pipeline(
                incident_id,
                payload.repo_name,
                payload.error_message,
                payload.stack_trace,
                claim_token=claim,
                commit_sha=payload.commit_sha,
            )
        except DatabaseUnavailableError as exc:
            raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "database unavailable") from exc
        return JSONResponse(status_code=200, content=IncidentResult(**result).model_dump(mode="json"))
    return IncidentAccepted(incident_id=str(incident_id), status="queued")


@router.get(
    "/incidents/{incident_id}",
    response_model=IncidentResult,
    dependencies=[Depends(authenticate)],
    tags=["incidents"],
    responses={**_ERRORS, 404: {"model": ErrorResponse}},
)
def read_incident(incident_id: uuid.UUID) -> IncidentResult:
    try:
        row = incident_service.get_incident_with_approval(incident_id)
    except DatabaseUnavailableError as exc:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "database unavailable") from exc
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "incident not found")
    return IncidentResult(**row)


@router.post(
    "/incidents/{incident_id}/remediation/decision",
    response_model=IncidentResult,
    tags=["remediation"],
    responses={**_ERRORS, 404: {"model": ErrorResponse}, 409: {"model": ErrorResponse}},
)
def decide_remediation(
    incident_id: uuid.UUID,
    body: RemediationDecision,
    principal: Annotated[Principal, Depends(require_role("reviewer"))],
) -> IncidentResult:
    """Approve or reject the PR proposed for an incident (roles: reviewer, admin).

    The request must name the pending approval and echo the SHA-256 of the proposed patch, so a decision can
    only apply to the exact change the reviewer saw. The reviewer is the authenticated identity, and it may not
    be the identity that submitted the incident (four-eyes rule). Decisions are final; a repeat returns 409.
    """
    try:
        row = incident_service.decide_remediation(
            incident_id, body.approval_id, body.patch_sha256, body.decision == "approve", principal.name, body.note
        )
    except IncidentNotFoundError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "incident not found") from exc
    except ApprovalError as exc:
        raise HTTPException(exc.http_status, str(exc)) from exc
    except DatabaseUnavailableError as exc:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "database unavailable") from exc
    return IncidentResult(**row)


@router.post(
    "/incidents/{incident_id}/retry",
    response_model=IncidentResult,
    tags=["incidents"],
    responses={**_ERRORS, 404: {"model": ErrorResponse}, 409: {"model": ErrorResponse}},
)
def retry_incident(
    incident_id: uuid.UUID, principal: Annotated[Principal, Depends(require_role("reviewer"))]
) -> IncidentResult:
    """Re-queue a `failed` incident (roles: reviewer, admin), e.g. after a provider quota reset."""
    try:
        row = incident_service.retry_incident(incident_id, principal.name)
    except IncidentNotFoundError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "incident not found") from exc
    except IncidentConflictError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    except DatabaseUnavailableError as exc:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "database unavailable") from exc
    return IncidentResult(**row)


@router.post(
    "/incidents/{incident_id}/ask",
    response_model=AskResponse,
    tags=["incidents"],
    responses={
        **_ERRORS,
        404: {"model": ErrorResponse},
        429: {"model": ErrorResponse, "description": "Question rate limit exceeded (see Retry-After)"},
        502: {"model": ErrorResponse, "description": "The LLM provider could not answer"},
        504: {"model": ErrorResponse, "description": "The LLM provider timed out"},
    },
)
def ask_about_incident(
    incident_id: uuid.UUID, body: AskRequest, principal: Annotated[Principal, Depends(rate_limited_asker)]
) -> AskResponse:
    """Ask a question about one incident (roles: reporter, reviewer, admin).

    Common questions (why not accepted, what to do next, what the patch changes) are answered by rules from the
    record without an LLM (`source=rules`); others go to the model, which must cite the record sections it used and
    whose quoted text is verified against the record. If the model is unavailable the deterministic guidance is
    returned (`degraded=true`). The server keeps no conversation state; `history` is client-held and untrusted. No
    answer triggers an action, and none was executed or verified.
    """
    if not settings.ask_enabled:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "question answering is disabled")
    try:
        result = ask_service.ask(incident_id, body.question, [turn.model_dump() for turn in body.history])
    except IncidentNotFoundError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "incident not found") from exc
    except DatabaseUnavailableError as exc:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "database unavailable") from exc
    except ask_service.AskUnavailableError as exc:
        raise HTTPException(exc.status, str(exc)) from exc
    logger.info("question answered for %s by %s", incident_id, principal.name)
    return AskResponse(**result)


@router.get(
    "/incidents/{incident_id}/trace",
    response_model=TraceResponse,
    dependencies=[Depends(authenticate)],
    tags=["incidents"],
    responses={**_ERRORS, 404: {"model": ErrorResponse}},
)
def incident_trace(incident_id: uuid.UUID) -> TraceResponse:
    """Versions, per-attempt latency/tokens and gate verdicts of one incident's run, from the stored record."""
    try:
        row = incident_service.get_incident_with_approval(incident_id)
    except DatabaseUnavailableError as exc:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "database unavailable") from exc
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "incident not found")
    return TraceResponse(**trace.build_trace(row))


@router.get(
    "/incidents/{incident_id}/evidence-graph",
    response_model=EvidenceGraphResponse,
    dependencies=[Depends(authenticate)],
    tags=["incidents"],
    responses={**_ERRORS, 404: {"model": ErrorResponse}},
)
def incident_evidence_graph(incident_id: uuid.UUID) -> EvidenceGraphResponse:
    """Claim -> evidence -> patch -> file links with a verification status on each piece of evidence and explicit
    flags for missing, unverified or conflicting evidence. Derived from the stored record; no LLM."""
    try:
        row = incident_service.get_incident_with_approval(incident_id)
    except DatabaseUnavailableError as exc:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "database unavailable") from exc
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "incident not found")
    return EvidenceGraphResponse(**evidence_graph.build_evidence_graph(row))


@router.get(
    "/incidents/{incident_id}/audit",
    response_model=AuditResponse,
    dependencies=[Depends(authenticate)],
    tags=["incidents"],
    responses={**_ERRORS, 404: {"model": ErrorResponse}},
)
def incident_audit(incident_id: uuid.UUID) -> AuditResponse:
    """Review timeline: submission, analysis versions, every proposal and who decided it (with the note), and the
    resulting pull request. Derived from stored rows; merging is never done by this service."""
    try:
        audit = incident_service.get_audit(incident_id)
    except DatabaseUnavailableError as exc:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "database unavailable") from exc
    if audit is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "incident not found")
    return AuditResponse(**audit)


@router.get(
    "/incidents/{incident_id}/guidance",
    response_model=GuidanceResponse,
    dependencies=[Depends(authenticate)],
    tags=["incidents"],
    responses={**_ERRORS, 404: {"model": ErrorResponse}},
)
def incident_guidance(incident_id: uuid.UUID) -> GuidanceResponse:
    """What state the incident is in, why, and what a person can do next - derived deterministically from the
    stored record and the runbook (no LLM, always available). Never claims a fix is correct."""
    try:
        row = incident_service.get_incident_with_approval(incident_id)
    except DatabaseUnavailableError as exc:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "database unavailable") from exc
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "incident not found")
    return GuidanceResponse(**guidance.build_guidance(row))
