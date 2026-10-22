"""Inbound integrations that cannot send an API key and authenticate with a webhook signature instead."""

from __future__ import annotations

import json
import logging
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from fastapi.responses import JSONResponse

from src.api.auth import Principal, rate_limited_reporter, submission_limiter
from src.api.schemas import AlertmanagerResult, AlertOutcome, ErrorResponse, IncidentAccepted, IncidentResult
from src.config import settings
from src.integrations import alertmanager, sentry
from src.services import incident_service
from src.services.incident_service import DatabaseUnavailableError, IncidentConflictError

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/integrations", tags=["integrations"])
SENTRY_IDENTITY = "sentry"


@router.post(
    "/sentry",
    status_code=status.HTTP_202_ACCEPTED,
    response_model=IncidentAccepted,
    responses={
        200: {"model": IncidentResult, "description": "Repeated delivery of an already-received event"},
        204: {"description": "Webhook resource that does not describe an error (ignored)"},
        401: {"model": ErrorResponse},
        403: {"model": ErrorResponse},
        404: {"model": ErrorResponse},
        413: {"model": ErrorResponse},
        422: {"model": ErrorResponse},
        429: {"model": ErrorResponse},
    },
)
async def sentry_webhook(request: Request) -> Response:
    """Sentry issue-alert / error webhooks. Authenticated by `Sentry-Hook-Signature` (HMAC of the body with the
    integration's Client Secret); the Sentry project must be mapped to an allow-listed repository."""
    if not settings.sentry_client_secret:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Sentry integration is not configured")
    declared = request.headers.get("content-length")
    if declared and declared.isdigit() and int(declared) > sentry.MAX_BODY_BYTES:
        raise HTTPException(status.HTTP_413_CONTENT_TOO_LARGE, "payload too large")
    body = await request.body()
    if len(body) > sentry.MAX_BODY_BYTES:
        raise HTTPException(status.HTTP_413_CONTENT_TOO_LARGE, "payload too large")
    if not sentry.signature_is_valid(settings.sentry_client_secret, body, request.headers.get("sentry-hook-signature")):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid webhook signature")

    if request.headers.get("sentry-hook-resource") not in sentry.SUPPORTED_RESOURCES:
        return Response(status_code=status.HTTP_204_NO_CONTENT)
    try:
        payload = json.loads(body)
        incident = sentry.to_incident(payload if isinstance(payload, dict) else {})
    except (ValueError, sentry.SentryPayloadError) as exc:
        message = str(exc) if isinstance(exc, sentry.SentryPayloadError) else "body is not valid JSON"
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, message) from exc

    repo_name = settings.sentry_project_repos.get(incident.project_id)
    if not repo_name:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT, "Sentry project is not mapped (SENTRY_PROJECT_REPOS)"
        )
    if not settings.is_repository_allowed(repo_name):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "mapped repository is not in ALLOWED_REPOSITORIES")
    retry_after = submission_limiter.check(SENTRY_IDENTITY, settings.rate_limit_per_minute)
    if retry_after is not None:
        raise HTTPException(
            status.HTTP_429_TOO_MANY_REQUESTS, "rate limit exceeded", headers={"Retry-After": str(int(retry_after) + 1)}
        )

    try:
        row, created = incident_service.submit_incident(
            incident.incident_id,
            repo_name,
            incident.error_message,
            incident.stack_trace,
            submitted_by=SENTRY_IDENTITY,
            commit_sha=incident.commit_sha,
        )
    except IncidentConflictError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    except DatabaseUnavailableError as exc:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "database unavailable") from exc
    if not created:
        return JSONResponse(status_code=200, content=IncidentResult(**row).model_dump(mode="json"))
    logger.info("Sentry event accepted as incident %s", incident.incident_id)
    return JSONResponse(
        status_code=202, content=IncidentAccepted(incident_id=str(incident.incident_id), status="queued").model_dump()
    )


@router.post(
    "/alertmanager",
    response_model=AlertmanagerResult,
    status_code=status.HTTP_202_ACCEPTED,
    responses={
        401: {"model": ErrorResponse},
        413: {"model": ErrorResponse},
        422: {"model": ErrorResponse},
        429: {"model": ErrorResponse},
        503: {"model": ErrorResponse},
    },
)
async def alertmanager_webhook(
    request: Request, principal: Annotated[Principal, Depends(rate_limited_reporter)]
) -> AlertmanagerResult:
    """Prometheus Alertmanager `webhook_config` receiver (payload version 4), authenticated with a reporter API key
    as a Bearer token. One incident per firing alert; the repository comes from the `ALERTMANAGER_REPO_LABEL`
    label (default `repository`) and must be allow-listed. Repeated notifications are idempotent."""
    declared = request.headers.get("content-length")
    if declared and declared.isdigit() and int(declared) > alertmanager.MAX_BODY_BYTES:
        raise HTTPException(status.HTTP_413_CONTENT_TOO_LARGE, "payload too large")
    body = await request.body()
    if len(body) > alertmanager.MAX_BODY_BYTES:
        raise HTTPException(status.HTTP_413_CONTENT_TOO_LARGE, "payload too large")
    try:
        payload = json.loads(body)
        incidents, dropped = alertmanager.to_incidents(
            payload if isinstance(payload, dict) else {}, settings.alertmanager_repo_label
        )
    except (ValueError, alertmanager.AlertmanagerPayloadError) as exc:
        message = str(exc) if isinstance(exc, alertmanager.AlertmanagerPayloadError) else "body is not valid JSON"
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, message) from exc

    outcomes = []
    for alert in incidents:
        if alert.repo_name is None:
            detail = f"label {settings.alertmanager_repo_label!r} missing or not 'owner/repo'"
            outcomes.append(AlertOutcome(alert=alert.alert_name, incident_id=None, outcome="skipped", detail=detail))
            continue
        if not settings.is_repository_allowed(alert.repo_name):
            outcomes.append(
                AlertOutcome(
                    alert=alert.alert_name, incident_id=None, outcome="skipped", detail="repository not allowed"
                )
            )
            continue
        try:
            _, created = incident_service.submit_incident(
                alert.incident_id,
                alert.repo_name,
                alert.error_message,
                alert.stack_trace,
                submitted_by=principal.name,
                commit_sha=alert.commit_sha,
            )
        except IncidentConflictError:
            outcomes.append(
                AlertOutcome(
                    alert=alert.alert_name,
                    incident_id=str(alert.incident_id),
                    outcome="skipped",
                    detail="incident id already used with different content",
                )
            )
            continue
        except DatabaseUnavailableError as exc:
            # Alertmanager retries on 5xx; the derived ids make the retry idempotent.
            raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "database unavailable") from exc
        outcomes.append(
            AlertOutcome(
                alert=alert.alert_name,
                incident_id=str(alert.incident_id),
                outcome="queued" if created else "duplicate",
            )
        )
    if dropped:
        logger.warning("Alertmanager notification: %d firing alerts beyond the limit were dropped", dropped)
    return AlertmanagerResult(alerts=outcomes, dropped=dropped)
