"""Application layer: incident intake, graph execution, persistence, remediation and approvals.

Incident status lifecycle (persisted in `incidents.status`; `error_category` says why, see src/errors.py):

    queued ──► processing ──► failed                 no usable analysis (LLM provider error, crash, interrupted)
                         ├──► needs_review           gate not passed (budget exhausted / insufficient evidence /
                         │                           no source / no code fix) or patch policy violated
                         ├──► analysis_ready         gate passed; PR not attempted (remediation disabled / no token)
                         ├──► awaiting_approval ──► pr_created | pr_skipped_duplicate | pr_failed   (approved)
                         │                     └──► remediation_rejected                             (rejected)
                         ├──► pr_created             (only when REQUIRE_REMEDIATION_APPROVAL=false)
                         ├──► pr_skipped_duplicate
                         └──► pr_failed

`queued` incidents are claimed by a worker (src/worker.py) under a lease; a crashed worker's incident is
re-queued when the lease expires and failed as `interrupted` after MAX_JOB_ATTEMPTS claims.

Side effects (DB writes, PR creation) happen here, never inside graph nodes, so a retried analysis attempt
cannot open a PR. Workflow state is not checkpointed: the graph runs to completion within one claim and
everything needed to resume (the analysis, the patch, the pending approval) is persisted in PostgreSQL.
"""

from __future__ import annotations

import logging
import re
import time
import uuid
from dataclasses import replace
from datetime import timedelta
from functools import lru_cache
from typing import Any, cast

from langgraph.graph.state import CompiledStateGraph
from sqlalchemy.exc import SQLAlchemyError

from src import metrics
from src.agent.graph import build_graph
from src.agent.parsing import compute_fingerprint
from src.agent.state import IncidentState, initial_state
from src.config import settings
from src.db import repositories
from src.errors import TRANSIENT_CATEGORIES, ErrorCategory
from src.integrations import github
from src.integrations.notify import notify_incident
from src.integrations.observability import build_run_config, flush_traces, incident_trace
from src.integrations.sandbox import run_patch_in_sandbox
from src.logging_config import incident_id_var, redact, request_id_var
from src.services import remediation_service
from src.services.remediation_service import ApprovalForbiddenError, ProposedFix, RemediationOutcome

logger = logging.getLogger(__name__)


class DatabaseUnavailableError(RuntimeError):
    pass


class IncidentConflictError(ValueError):
    """The client reused an incident id for a different repository."""


class IncidentNotFoundError(LookupError):
    pass


@lru_cache(maxsize=1)
def get_graph() -> CompiledStateGraph[Any, Any, Any, Any]:
    return build_graph(github.fetch_source_context, repositories.find_similar_incidents)


def submit_incident(
    incident_id: uuid.UUID,
    repo_name: str,
    error_message: str,
    stack_trace: str,
    submitted_by: str | None = None,
    commit_sha: str | None = None,
) -> tuple[dict[str, Any], bool]:
    """Persist a new `queued` incident, or return the existing one for a repeated delivery."""
    fingerprint = compute_fingerprint(repo_name, error_message, stack_trace)
    try:
        row, created = repositories.create_incident(
            incident_id,
            repo_name,
            fingerprint,
            error_message,
            stack_trace,
            submitted_by,
            request_id_var.get(),
            commit_sha,
        )
    except SQLAlchemyError as exc:
        logger.error("database unavailable while creating incident: %s", type(exc).__name__)
        raise DatabaseUnavailableError("database unavailable") from exc
    if not created and row["repo_name"] != repo_name:
        raise IncidentConflictError("incident_id already used for a different repository")
    return row, created


def _final_status(final: IncidentState) -> tuple[str, str, str | None]:
    if final["workflow_status"] == "failed":
        return "failed", final["error"] or "analysis failed", final["error_category"]
    decision = ((final["root_cause_analysis"] or {}).get("evaluation") or {}).get("decision")
    category = final["error_category"] or ErrorCategory.RETRY_BUDGET_EXHAUSTED.value
    return "needs_review", decision or "analysis did not pass the quality gate", category


def _defer_transient(incident_id: uuid.UUID, claim_token: str, category: str | None) -> bool:
    """Re-queue with exponential backoff (1, 2, 4 ... min, max 15) instead of failing on a transient error."""
    if category not in TRANSIENT_CATEGORIES:
        return False
    try:
        row = repositories.get_incident(incident_id)
        attempts = row["job_attempts"] if row else settings.max_transient_retries
        if attempts >= settings.max_transient_retries:
            return False
        delay = timedelta(seconds=min(60 * 2 ** max(attempts - 1, 0), 900))
        repositories.defer_incident(
            incident_id,
            claim_token,
            delay,
            f"{category}: retrying in {int(delay.total_seconds())}s "
            f"(claim {attempts}/{settings.max_transient_retries})",
            category,
        )
    except SQLAlchemyError as exc:
        raise DatabaseUnavailableError("database unavailable") from exc
    logger.warning("transient failure %s: re-queued with %ss backoff", category, int(delay.total_seconds()))
    metrics.JOBS_DEFERRED.labels(category).inc()
    return True


def retry_incident(incident_id: uuid.UUID, actor: str) -> dict[str, Any]:
    """Manual retry of a failed incident (e.g. after fixing a provider key or once a quota resets)."""
    try:
        if repositories.get_incident(incident_id) is None:
            raise IncidentNotFoundError("incident not found")
        if not repositories.requeue_failed(incident_id, f"re-queued by {actor}"):
            raise IncidentConflictError("only failed incidents can be retried")
    except SQLAlchemyError as exc:
        raise DatabaseUnavailableError("database unavailable") from exc
    logger.info("incident %s re-queued by %s", incident_id, actor)
    return _load(incident_id)


def _fix_from_state(incident_id: uuid.UUID, fingerprint: str, final: IncidentState) -> ProposedFix:
    return ProposedFix(
        incident_id=incident_id,
        repo_name=final["repo_name"],
        fingerprint=fingerprint,
        target_file=final["affected_file"],
        patch=final["suggested_patch"],
        analysis=final["root_cause_analysis"] or {},
        quality_score=final["quality_score"],
        iterations=final["iterations"],
    )


def _outcome_fields(outcome: RemediationOutcome) -> dict[str, Any]:
    fields: dict[str, Any] = {
        "status": outcome.status,
        "status_reason": redact(outcome.reason),
        "error_category": outcome.category.value if outcome.category else None,
    }
    if outcome.pr_url:
        fields.update(pr_url=outcome.pr_url, pr_number=outcome.pr_number, pr_branch=outcome.branch)
    return fields


def run_incident_pipeline(
    incident_id: uuid.UUID,
    repo_name: str,
    error_message: str,
    stack_trace: str,
    graph: CompiledStateGraph[Any, Any, Any, Any] | None = None,
    claim_token: str | None = None,
    commit_sha: str | None = None,
) -> dict[str, Any]:
    """Run the graph, persist the outcome, optionally propose/open a PR. Returns the stored incident.

    With `claim_token`, every write requires that claim to still own the incident; if another worker took it
    over (expired lease), this run's result is discarded before any remediation side effect.

    Raises DatabaseUnavailableError when the outcome cannot be persisted; no PR is attempted in that case,
    because the duplicate-PR guard and the approval record depend on the database.
    """
    token = incident_id_var.set(str(incident_id))
    fingerprint = compute_fingerprint(repo_name, error_message, stack_trace)
    started = time.perf_counter()
    try:
        with incident_trace(str(incident_id), repo_name, fingerprint) as trace:
            try:
                result = _run(
                    incident_id,
                    repo_name,
                    error_message,
                    stack_trace,
                    fingerprint,
                    graph or get_graph(),
                    claim_token,
                    commit_sha,
                )
            except repositories.ClaimLostError:
                logger.warning("claim lost to another worker; discarding this run's result")
                trace.update(error="claim_lost")
                return _load(incident_id)
            metrics.PIPELINE_SECONDS.observe(time.perf_counter() - started)
            if result["status"] != "queued":  # a deferred retry has not finished yet
                metrics.record_incident(result["status"], result["error_category"])
            trace.update(
                output={"status": result["status"], "quality_score": result["quality_score"]},
                metadata={"attempts": result["iterations"], "error_category": result["error_category"]},
                error=result["error_category"] if result["status"] == "failed" else None,
            )
            return result
    finally:
        flush_traces()
        incident_id_var.reset(token)


def _run(
    incident_id: uuid.UUID,
    repo_name: str,
    error_message: str,
    stack_trace: str,
    fingerprint: str,
    graph: CompiledStateGraph[Any, Any, Any, Any],
    claim_token: str | None = None,
    commit_sha: str | None = None,
) -> dict[str, Any]:
    state = initial_state(str(incident_id), error_message, stack_trace, repo_name, commit_sha)
    try:
        final = cast(
            IncidentState, graph.invoke(state, config=build_run_config(str(incident_id), repo_name, fingerprint))
        )
    except Exception:  # background-job boundary: any crash is logged and recorded as `failed`, never swallowed
        logger.exception("workflow crashed")
        _persist_or_raise(
            incident_id,
            claim_token=claim_token,
            status="failed",
            status_reason="internal error during analysis",
            error_category=ErrorCategory.INTERNAL_ERROR.value,
        )
        return _load(incident_id)

    _persist_or_raise(
        incident_id,
        claim_token=claim_token,
        error_message=final["error_message"],
        stack_trace=final["stack_trace"],
        affected_file=final["affected_file"],
        root_cause_analysis={
            **(final["root_cause_analysis"] or {}),
            "attempts": final["attempts"],
            "source_windows": source_windows(final),
            "source_revision": source_revision(final),
        },
        suggested_patch=final["suggested_patch"],
        quality_score=final["quality_score"],
        iterations=final["iterations"],
    )
    if final["quality_gate_passed"]:
        fix = _fix_from_state(incident_id, fingerprint, final)
        # Only this service writes `sandbox`; never trust a key of that name that came from the model.
        fix = replace(fix, analysis={k: v for k, v in fix.analysis.items() if k != "sandbox"})
        if settings.sandbox_enabled and fix.patch and fix.target_file:
            report = run_patch_in_sandbox(fix.repo_name, fix.target_file, fix.patch)
            logger.info("sandbox run: %s", report.status)
            fix = replace(fix, analysis={**fix.analysis, "sandbox": report.as_dict()})
            _persist_or_raise(
                incident_id,
                claim_token=claim_token,
                root_cause_analysis={
                    **(final["root_cause_analysis"] or {}),
                    "attempts": final["attempts"],
                    "source_windows": source_windows(final),
                    "source_revision": source_revision(final),
                    "sandbox": report.as_dict(),
                },
            )
        try:
            outcome = remediation_service.remediate(fix)
        except SQLAlchemyError as exc:
            logger.error("database unavailable during remediation: %s", type(exc).__name__)
            raise DatabaseUnavailableError("database unavailable") from exc
        _persist_or_raise(incident_id, claim_token=claim_token, **_outcome_fields(outcome))
    else:
        status, reason, category = _final_status(final)
        if status == "failed" and claim_token and _defer_transient(incident_id, claim_token, category):
            return _load(incident_id)
        _persist_or_raise(
            incident_id, claim_token=claim_token, status=status, status_reason=redact(reason), error_category=category
        )
    result = _load(incident_id)
    notify_incident(result)
    logger.info(
        "incident finished: status=%s score=%.2f attempts=%d",
        result["status"],
        result["quality_score"],
        result["iterations"],
    )
    return result


def list_incidents(
    status: str | None, repo_name: str | None, limit: int, cursor: str | None
) -> tuple[list[dict[str, Any]], str | None]:
    try:
        return repositories.list_incidents(status, repo_name, limit, cursor)
    except SQLAlchemyError as exc:
        raise DatabaseUnavailableError("database unavailable") from exc


def get_incident_with_approval(incident_id: uuid.UUID) -> dict[str, Any] | None:
    try:
        row = repositories.get_incident(incident_id)
        if row is None:
            return None
        row["pending_approval"] = repositories.get_pending_approval(incident_id)
        return row
    except SQLAlchemyError as exc:
        raise DatabaseUnavailableError("database unavailable") from exc


_WINDOW_LIMIT = 40_000


def source_windows(final: IncidentState) -> dict[str, str]:
    """The code the model was shown (trigger window, caller windows), redacted and capped, so the evidence trail can
    re-verify quotes later. Kept inside the stored analysis, never returned by the public incident endpoints."""
    return {
        key: redact(str(final[key]))[:_WINDOW_LIMIT] for key in ("code_context", "caller_context") if final.get(key)
    }


_COMMIT_LINE = re.compile(r"^COMMIT: ([0-9a-f]{7,40})", re.MULTILINE)


def source_revision(final: IncidentState) -> dict[str, Any]:
    """Which revision of the code the analysis read: the commit shown to the model, and whether the incident pinned it
    (the deployed commit) or it was the default branch head at analysis time (which may have moved since)."""
    match = _COMMIT_LINE.search(final.get("code_context") or "")
    return {"commit": match.group(1) if match else None, "pinned": bool(final.get("commit_sha"))}


def get_audit(incident_id: uuid.UUID) -> dict[str, Any] | None:
    """The review timeline of one incident, or None if it does not exist."""
    from src.services.audit import build_audit

    try:
        row = repositories.get_incident(incident_id)
        if row is None:
            return None
        return build_audit(row, repositories.list_approvals(incident_id))
    except SQLAlchemyError as exc:
        raise DatabaseUnavailableError("database unavailable") from exc


def decide_remediation(
    incident_id: uuid.UUID, approval_id: uuid.UUID, patch_sha256: str, approve: bool, reviewer: str, note: str | None
) -> dict[str, Any]:
    """Apply a human approval/rejection. Raises IncidentNotFoundError, ApprovalError, DatabaseUnavailableError."""
    token = incident_id_var.set(str(incident_id))
    try:
        row = repositories.get_incident(incident_id)
        if row is None:
            raise IncidentNotFoundError("incident not found")
        fix = ProposedFix(
            incident_id=incident_id,
            repo_name=row["repo_name"],
            fingerprint=row["fingerprint"],
            target_file=row["affected_file"],
            patch=row["suggested_patch"],
            analysis=row["analysis"] or {},
            quality_score=row["quality_score"],
            iterations=row["iterations"],
        )
        if not settings.allow_self_approval and row["submitted_by"] and row["submitted_by"] == reviewer:
            raise ApprovalForbiddenError("the identity that submitted an incident may not decide its remediation")
        outcome = remediation_service.decide(fix, approval_id, patch_sha256, approve, reviewer, note)
        logger.info("remediation decision applied: approve=%s -> %s", approve, outcome.status)
        _persist_or_raise(incident_id, **_outcome_fields(outcome))
        metrics.record_decision(approve)
        metrics.record_incident(outcome.status, outcome.category.value if outcome.category else None)
        updated = _load(incident_id)
        notify_incident(updated)
        return updated
    except SQLAlchemyError as exc:
        raise DatabaseUnavailableError("database unavailable") from exc
    finally:
        flush_traces()
        incident_id_var.reset(token)


def claim_for_inline_run(incident_id: uuid.UUID) -> str | None:
    """Used by `wait=true`: take the incident off the queue before any worker does. Returns the claim token."""
    try:
        return repositories.claim_incident(incident_id, timedelta(seconds=settings.job_lease_seconds))
    except SQLAlchemyError as exc:
        raise DatabaseUnavailableError("database unavailable") from exc


def _persist_or_raise(incident_id: uuid.UUID, claim_token: str | None = None, **fields: Any) -> None:
    try:
        repositories.update_incident(incident_id, expected_claim_token=claim_token, **fields)
    except SQLAlchemyError as exc:
        logger.error("database unavailable while saving incident: %s", type(exc).__name__)
        raise DatabaseUnavailableError("database unavailable") from exc


def _load(incident_id: uuid.UUID) -> dict[str, Any]:
    row = get_incident_with_approval(incident_id)
    if row is None:
        raise DatabaseUnavailableError("incident row disappeared")
    return row
