"""Incident / approval persistence and historical-incident candidates.

All queries are parameterized SQLAlchemy statements. Every historical query is filtered by repository in SQL,
so one repository's incidents can never be offered as context for another.
"""

from __future__ import annotations

import base64
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import CursorResult, and_, func, or_, select, update
from sqlalchemy.exc import IntegrityError

from src.db.client import session_scope
from src.db.models import Incident, RemediationApproval
from src.retrieval.history import HistoryCandidate, HistoryQuery, rank_lexical, to_prompt_records

HISTORY_CANDIDATE_LIMIT = 200  # newest eligible incidents per repository considered for ranking
HISTORY_ELIGIBLE_STATUSES = (
    "analysis_ready",
    "awaiting_approval",
    "pr_created",
    "pr_skipped_duplicate",
    "pr_failed",
    "remediation_rejected",
)


def _iso(dt: datetime | None) -> str | None:
    return dt.isoformat() if dt else None


def _aware(dt: datetime) -> datetime:
    """SQLite returns naive datetimes even for timezone=True columns; treat them as UTC."""
    return dt if dt.tzinfo else dt.replace(tzinfo=UTC)


def incident_to_dict(row: Incident) -> dict[str, Any]:
    return {
        "incident_id": str(row.id),
        "repo_name": row.repo_name,
        "fingerprint": row.fingerprint,
        "status": row.status,
        "status_reason": row.status_reason,
        "error_category": row.error_category,
        "error_message": row.error_message,
        "submitted_by": row.submitted_by,
        "request_id": row.request_id,
        "job_attempts": row.job_attempts,
        "available_at": _iso(_aware(row.available_at)) if row.available_at else None,
        "affected_file": row.affected_file,
        "quality_score": row.quality_score,
        "iterations": row.iterations,
        "analysis": row.root_cause_analysis,
        "suggested_patch": row.suggested_patch,
        "pr_url": row.pr_url,
        "pr_number": row.pr_number,
        "pr_branch": row.pr_branch,
        "created_at": _iso(row.created_at),
        "updated_at": _iso(row.updated_at),
    }


def approval_to_dict(row: RemediationApproval) -> dict[str, Any]:
    return {
        "approval_id": str(row.id),
        "incident_id": str(row.incident_id),
        "action": row.action,
        "target_file": row.target_file,
        "patch_sha256": row.patch_sha256,
        "status": row.status,
        "reviewer": row.reviewer,
        "decision_note": row.decision_note,
        "created_at": _iso(row.created_at),
        "expires_at": _iso(_aware(row.expires_at)),
        "decided_at": _iso(row.decided_at),
    }


# ------------------------------------------------------------------ incidents


def create_incident(
    incident_id: uuid.UUID,
    repo_name: str,
    fingerprint: str,
    error_message: str,
    stack_trace: str,
    submitted_by: str | None = None,
    request_id: str | None = None,
) -> tuple[dict[str, Any], bool]:
    """Insert a `processing` row. Idempotent per id: returns (row, created)."""
    try:
        with session_scope() as session:
            existing = session.get(Incident, incident_id)
            if existing is not None:
                return incident_to_dict(existing), False
            row = Incident(
                id=incident_id,
                repo_name=repo_name,
                fingerprint=fingerprint,
                error_message=error_message,
                stack_trace=stack_trace,
                status="queued",
                submitted_by=submitted_by,
                request_id=request_id,
            )
            session.add(row)
            session.flush()
            return incident_to_dict(row), True
    except IntegrityError:  # concurrent delivery with the same id won the insert race
        current = get_incident(incident_id)
        if current is None:
            raise
        return current, False


def update_incident(incident_id: uuid.UUID, expected_claim_token: str | None = None, **fields: Any) -> None:
    """Update named columns of an existing incident (attribute names from `Incident`).

    With `expected_claim_token`, the write only happens while that claim still owns the `processing` incident;
    otherwise ClaimLostError is raised and nothing is written.
    """
    with session_scope() as session:
        row = session.get(Incident, incident_id, with_for_update=expected_claim_token is not None)
        if row is None:
            raise LookupError(f"incident {incident_id} does not exist")
        if expected_claim_token is not None and (row.status != "processing" or row.claim_token != expected_claim_token):
            raise ClaimLostError(f"claim on incident {incident_id} was lost")
        for name, value in fields.items():
            if not hasattr(Incident, name):
                raise AttributeError(f"unknown incident field {name!r}")
            setattr(row, name, value)


def get_incident(incident_id: uuid.UUID) -> dict[str, Any] | None:
    with session_scope() as session:
        row = session.get(Incident, incident_id)
        return incident_to_dict(row) if row else None


class InvalidCursorError(ValueError):
    pass


def _encode_cursor(row: Incident) -> str:
    raw = f"{_aware(row.created_at).isoformat()}|{row.id}"
    return base64.urlsafe_b64encode(raw.encode()).decode()


def _decode_cursor(cursor: str) -> tuple[datetime, uuid.UUID]:
    try:
        created, ident = base64.urlsafe_b64decode(cursor.encode()).decode().split("|")
        return datetime.fromisoformat(created), uuid.UUID(ident)
    except (ValueError, UnicodeDecodeError) as exc:
        raise InvalidCursorError("invalid cursor") from exc


def list_incidents(
    status: str | None, repo_name: str | None, limit: int, cursor: str | None
) -> tuple[list[dict[str, Any]], str | None]:
    """Newest first, keyset-paginated on (created_at, id) so pages stay stable while new incidents arrive."""
    stmt = select(Incident).order_by(Incident.created_at.desc(), Incident.id.desc()).limit(limit + 1)
    if status:
        stmt = stmt.where(Incident.status == status)
    if repo_name:
        stmt = stmt.where(Incident.repo_name == repo_name)
    if cursor:
        created, ident = _decode_cursor(cursor)
        stmt = stmt.where(or_(Incident.created_at < created, and_(Incident.created_at == created, Incident.id < ident)))
    with session_scope() as session:
        rows = list(session.scalars(stmt))
        next_cursor = _encode_cursor(rows[limit - 1]) if len(rows) > limit else None
        return [incident_summary(r) for r in rows[:limit]], next_cursor


def incident_summary(row: Incident) -> dict[str, Any]:
    return {
        "incident_id": str(row.id),
        "repo_name": row.repo_name,
        "status": row.status,
        "error_category": row.error_category,
        "quality_score": row.quality_score,
        "iterations": row.iterations,
        "affected_file": row.affected_file,
        "submitted_by": row.submitted_by,
        "pr_url": row.pr_url,
        "created_at": _iso(_aware(row.created_at)),
        "updated_at": _iso(_aware(row.updated_at)),
    }


# ------------------------------------------------------------------ history


def _root_cause_summary(analysis: dict[str, Any] | None) -> str:
    if not analysis:
        return ""
    try:
        return str(analysis["diagnostic_chain"]["primary_root_cause"]["technical_explanation"])
    except (KeyError, TypeError):
        return ""


def load_history_candidates(repo_name: str, exclude_id: str | None = None) -> list[HistoryCandidate]:
    """Newest eligible incidents of ONE repository (bounded), newest first."""
    stmt = (
        select(Incident)
        .where(Incident.repo_name == repo_name, Incident.status.in_(HISTORY_ELIGIBLE_STATUSES))
        .order_by(Incident.created_at.desc())
        .limit(HISTORY_CANDIDATE_LIMIT)
    )
    if exclude_id:
        stmt = stmt.where(Incident.id != uuid.UUID(exclude_id))
    with session_scope() as session:
        rows = list(session.scalars(stmt))
    return [
        HistoryCandidate(
            id=str(row.id),
            repo_name=row.repo_name,
            fingerprint=row.fingerprint,
            error_message=row.error_message[:2000],
            stack_trace=row.stack_trace[:8000],
            affected_file=row.affected_file,
            extra={
                "root_cause_summary": _root_cause_summary(row.root_cause_analysis)[:800],
                "suggested_patch": (row.suggested_patch or "")[:2000],
                "status": row.status,
                "pr_url": row.pr_url,
                "created_at": _iso(row.created_at),
            },
        )
        for row in rows
    ]


def find_similar_incidents(query: HistoryQuery, exclude_id: str | None = None, limit: int = 5) -> list[dict[str, Any]]:
    """Ranked, explained matches (strategy lexical-v1) for the prompt."""
    return to_prompt_records(rank_lexical(query, load_history_candidates(query.repo_name, exclude_id), limit))


# ------------------------------------------------------------------ job queue
#
# The incidents table doubles as a durable queue: `queued` rows are claimed with a compare-and-set update
# (status queued -> processing), which works on PostgreSQL and SQLite and lets several workers run safely.
# A claim is a lease; if the worker dies, the lease expires and the incident is queued again (bounded).


class ClaimLostError(RuntimeError):
    """This worker's claim expired and another worker now owns the incident; its result must be discarded."""


def claim_incident(incident_id: uuid.UUID, lease: timedelta) -> str | None:
    """Atomically move one `queued` incident to `processing`. Returns this claim's token, or None if lost."""
    now = datetime.now(UTC)
    token = uuid.uuid4().hex
    with session_scope() as session:
        result: CursorResult[Any] = session.execute(  # type: ignore[assignment]
            update(Incident)
            .where(Incident.id == incident_id, Incident.status == "queued")
            .values(
                status="processing",
                job_attempts=Incident.job_attempts + 1,
                lease_expires_at=now + lease,
                claim_token=token,
                updated_at=now,
            )
        )
        return token if result.rowcount == 1 else None


def renew_lease(incident_id: uuid.UUID, claim_token: str, lease: timedelta) -> bool:
    """Extend this worker's claim. False once the incident finished or another worker reclaimed it."""
    now = datetime.now(UTC)
    with session_scope() as session:
        result: CursorResult[Any] = session.execute(  # type: ignore[assignment]
            update(Incident)
            .where(
                Incident.id == incident_id,
                Incident.status == "processing",
                Incident.claim_token == claim_token,
            )
            .values(lease_expires_at=now + lease)
        )
        return result.rowcount == 1


def defer_incident(incident_id: uuid.UUID, claim_token: str, delay: timedelta, reason: str, category: str) -> None:
    """Give a claimed incident back to the queue, not claimable before now + delay (transient failures)."""
    now = datetime.now(UTC)
    with session_scope() as session:
        result: CursorResult[Any] = session.execute(  # type: ignore[assignment]
            update(Incident)
            .where(Incident.id == incident_id, Incident.status == "processing", Incident.claim_token == claim_token)
            .values(
                status="queued",
                available_at=now + delay,
                lease_expires_at=None,
                claim_token=None,
                status_reason=reason,
                error_category=category,
                updated_at=now,
            )
        )
        if result.rowcount != 1:
            raise ClaimLostError(f"claim on incident {incident_id} was lost")


def requeue_failed(incident_id: uuid.UUID, reason: str) -> bool:
    """Manual retry: put a `failed` incident back in the queue with a fresh claim budget."""
    now = datetime.now(UTC)
    with session_scope() as session:
        result: CursorResult[Any] = session.execute(  # type: ignore[assignment]
            update(Incident)
            .where(Incident.id == incident_id, Incident.status == "failed")
            .values(
                status="queued",
                job_attempts=0,
                available_at=None,
                lease_expires_at=None,
                claim_token=None,
                status_reason=reason,
                error_category=None,
                updated_at=now,
            )
        )
        return result.rowcount == 1


def claim_next(lease: timedelta, scan: int = 10) -> dict[str, Any] | None:
    """Claim the oldest queued incident. Returns the job (id, repo, error, trace, attempts) or None if empty."""
    now = datetime.now(UTC)
    stmt = (
        select(Incident.id)
        .where(
            Incident.status == "queued",
            or_(Incident.available_at.is_(None), Incident.available_at <= now),
        )
        .order_by(Incident.created_at)
        .limit(scan)
    )
    with session_scope() as session:
        candidates = list(session.scalars(stmt))
    for incident_id in candidates:  # another worker may win a race for any single row; try the next one
        token = claim_incident(incident_id, lease)
        if token:
            with session_scope() as session:
                row = session.get(Incident, incident_id)
                if row is not None:
                    return {
                        "claim_token": token,
                        "incident_id": row.id,
                        "repo_name": row.repo_name,
                        "error_message": row.error_message,
                        "stack_trace": row.stack_trace,
                        "job_attempts": row.job_attempts,
                    }
    return None


def requeue_expired(max_attempts: int, lease: timedelta, reason: str, category: str) -> tuple[int, int]:
    """Return expired `processing` claims to the queue, or fail them after `max_attempts` claims.

    Rows without a lease (created before migration 0004) count as expired once they are older than `lease`.
    Returns (requeued, failed).
    """
    now = datetime.now(UTC)
    expired = and_(
        Incident.status == "processing",
        or_(
            Incident.lease_expires_at < now,
            and_(Incident.lease_expires_at.is_(None), Incident.updated_at < now - lease),
        ),
    )
    with session_scope() as session:
        failed: CursorResult[Any] = session.execute(  # type: ignore[assignment]
            update(Incident)
            .where(expired, Incident.job_attempts >= max_attempts)
            .values(
                status="failed", status_reason=reason, error_category=category, lease_expires_at=None, updated_at=now
            )
        )
        requeued: CursorResult[Any] = session.execute(  # type: ignore[assignment]
            update(Incident)
            .where(expired, Incident.job_attempts < max_attempts)
            .values(status="queued", lease_expires_at=None, updated_at=now)
        )
        return requeued.rowcount, failed.rowcount


def queue_depth() -> int:
    with session_scope() as session:
        return session.scalar(select(func.count()).select_from(Incident).where(Incident.status == "queued")) or 0


def find_recent_pr(repo_name: str, fingerprint: str, hours: int = 24) -> str | None:
    """URL of a PR already opened for the same failure fingerprint within `hours`."""
    since = datetime.now(UTC) - timedelta(hours=hours)
    stmt = (
        select(Incident.pr_url)
        .where(
            Incident.repo_name == repo_name,
            Incident.fingerprint == fingerprint,
            Incident.pr_url.is_not(None),
            Incident.created_at >= since,
        )
        .order_by(Incident.created_at.desc())
        .limit(1)
    )
    with session_scope() as session:
        return session.scalars(stmt).first()


# ------------------------------------------------------------------ approvals


def create_approval(incident_id: uuid.UUID, target_file: str, patch_sha256: str, ttl_hours: int) -> dict[str, Any]:
    """New pending approval; any older pending approval of the same incident is superseded."""
    now = datetime.now(UTC)
    with session_scope() as session:
        session.execute(
            update(RemediationApproval)
            .where(RemediationApproval.incident_id == incident_id, RemediationApproval.status == "pending")
            .values(status="superseded", decided_at=now)
        )
        row = RemediationApproval(
            id=uuid.uuid4(),
            incident_id=incident_id,
            target_file=target_file,
            patch_sha256=patch_sha256,
            status="pending",
            created_at=now,
            expires_at=now + timedelta(hours=ttl_hours),
        )
        session.add(row)
        session.flush()
        return approval_to_dict(row)


def get_approval(approval_id: uuid.UUID) -> dict[str, Any] | None:
    with session_scope() as session:
        row = session.get(RemediationApproval, approval_id)
        return approval_to_dict(row) if row else None


def list_approvals(incident_id: uuid.UUID) -> list[dict[str, Any]]:
    """Every approval of an incident (pending, decided, expired, superseded), oldest first."""
    stmt = (
        select(RemediationApproval)
        .where(RemediationApproval.incident_id == incident_id)
        .order_by(RemediationApproval.created_at)
    )
    with session_scope() as session:
        return [approval_to_dict(row) for row in session.scalars(stmt)]


def get_pending_approval(incident_id: uuid.UUID) -> dict[str, Any] | None:
    stmt = select(RemediationApproval).where(
        RemediationApproval.incident_id == incident_id, RemediationApproval.status == "pending"
    )
    with session_scope() as session:
        row = session.scalars(stmt).first()
        return approval_to_dict(row) if row else None


def transition_approval(
    approval_id: uuid.UUID, from_status: str, to_status: str, reviewer: str | None = None, note: str | None = None
) -> bool:
    """Compare-and-set status change. Returns False if another request changed it first (no double execution)."""
    values: dict[str, Any] = {"status": to_status, "decided_at": datetime.now(UTC)}
    if reviewer is not None:
        values["reviewer"] = reviewer
    if note is not None:
        values["decision_note"] = note
    with session_scope() as session:
        result: CursorResult[Any] = session.execute(  # type: ignore[assignment]
            update(RemediationApproval)
            .where(RemediationApproval.id == approval_id, RemediationApproval.status == from_status)
            .values(**values)
        )
        return result.rowcount == 1
