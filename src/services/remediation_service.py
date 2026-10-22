"""Turns an accepted analysis into a human-reviewed GitHub pull request - or explains why it did not.

Policy, enforced in code (the LLM never decides whether a side effect is allowed):
  1. the deterministic quality gate passed;
  2. ENABLE_GITHUB_REMEDIATION=true and a GITHUB_TOKEN is configured;
  3. the patch is a parseable single-file diff whose header path equals the retrieved file, outside protected
     paths, within MAX_PATCH_CHANGED_LINES;
  4. with REQUIRE_REMEDIATION_APPROVAL=true (default) a human must explicitly approve the exact patch
     (bound by SHA-256) before any GitHub write; no decision means no action, and approvals expire;
  5. no PR for the same failure fingerprint in the last 24 h (DB) and no open PR on the deterministic
     remediation branch (GitHub). Approval transitions are compare-and-set, so a resumed or repeated
     approval cannot execute twice.
"""

from __future__ import annotations

import hashlib
import logging
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from src.agent.patching import PatchError, parse_unified_diff
from src.config import settings
from src.db import repositories
from src.errors import ErrorCategory
from src.integrations.github import FORBIDDEN_PATH_PREFIXES, GitHubError, create_fix_pull_request
from src.integrations.observability import observe
from src.logging_config import find_secrets, redact

logger = logging.getLogger(__name__)


class PatchRejectedError(ValueError):
    pass


class ApprovalError(Exception):
    """Base for approval decision failures; `http_status` is used by the API layer."""

    http_status = 409


class ApprovalNotFoundError(ApprovalError):
    http_status = 404


class ApprovalForbiddenError(ApprovalError):
    http_status = 403


class ApprovalConflictError(ApprovalError):
    """Already decided, superseded, expired or bound to a different patch."""


@dataclass(frozen=True)
class ProposedFix:
    """Everything needed to open a PR, from either the graph state or the stored incident."""

    incident_id: uuid.UUID
    repo_name: str
    fingerprint: str
    target_file: str | None
    patch: str | None
    analysis: dict[str, Any]
    quality_score: float
    iterations: int


@dataclass(frozen=True)
class RemediationOutcome:
    status: str  # analysis_ready | needs_review | awaiting_approval | pr_created | pr_skipped_duplicate | pr_failed
    reason: str
    category: ErrorCategory | None = None
    pr_url: str | None = None
    pr_number: int | None = None
    branch: str | None = None
    approval: dict[str, Any] | None = None


def patch_sha256(patch: str | None) -> str:
    return hashlib.sha256((patch or "").encode("utf-8")).hexdigest()


def validate_patch_for_pr(patch: str | None, target_file: str | None, max_changed_lines: int) -> None:
    """Reject anything but a small, single-file diff for exactly the file we retrieved."""
    if not patch or not target_file:
        raise PatchRejectedError("no patch or no target file")
    try:
        parsed = parse_unified_diff(patch)
    except PatchError as exc:
        raise PatchRejectedError(f"malformed diff: {exc}") from exc
    if parsed.file_count != 1:
        raise PatchRejectedError("diff must modify exactly one file")
    if (parsed.path or "").strip("/") != target_file.strip("/"):
        raise PatchRejectedError("diff header path does not match the retrieved file")
    if target_file.startswith(FORBIDDEN_PATH_PREFIXES) or ".." in target_file.split("/"):
        raise PatchRejectedError("target file is in a protected path")
    if parsed.changed_lines > max_changed_lines:
        raise PatchRejectedError(f"diff changes {parsed.changed_lines} lines (limit {max_changed_lines})")
    added = "\n".join(line[1:] for line in patch.splitlines() if line.startswith("+") and not line.startswith("+++"))
    if secrets := find_secrets(added):
        raise PatchRejectedError("patch adds what looks like a credential: " + ", ".join(secrets))


def _untrusted(text: Any, limit: int = 2000) -> str:
    """LLM text for Markdown: no accidental @-mentions, bounded length."""
    value = str(text or "n/a").replace("@", "@​")
    value = redact(value)
    return value if len(value) <= limit else value[:limit] + " ..."


def _revision_line(analysis: dict[str, Any]) -> str:
    """Which code the analysis and the quality gate saw. The PR itself is built on the *current* default branch head."""
    revision = analysis.get("source_revision")
    if not isinstance(revision, dict):
        return ""
    commit = revision.get("commit")
    if not isinstance(commit, str) or not commit:
        return ""
    origin = (
        "the deployed commit reported with the incident"
        if revision.get("pinned")
        else "the default branch head when the incident was analysed (it may have moved since)"
    )
    return f"- **Analysed at commit:** `{_untrusted(commit, 40)}` ({origin})"


def build_pull_request_title(analysis: dict[str, Any]) -> str:
    title = (analysis.get("incident_summary") or {}).get("title") or "Proposed incident fix"
    return "[OpsPulse] " + " ".join(_untrusted(title, 120).split())


def _tests_notice(sandbox: Any) -> str:
    if not isinstance(sandbox, dict) or sandbox.get("status") in (None, "unavailable", "error"):
        return "**No tests were executed by OpsPulse AI.**"
    summary = _untrusted(sandbox.get("summary"), 200)
    return (
        f"The repository's own tests were run once in an isolated sandbox: **{summary}** (not proof of a correct fix)."
    )


def build_pull_request_body(fix: ProposedFix, reviewer: str | None = None) -> str:
    analysis = fix.analysis
    summary = analysis.get("incident_summary") or {}
    root = (analysis.get("diagnostic_chain") or {}).get("primary_root_cause") or {}
    remediation = analysis.get("patch_remediation") or {}
    evaluation = analysis.get("evaluation") or {}
    evidence_lines = [
        f"- **{_untrusted(e.get('kind'), 20)}** ({_untrusted(e.get('source'), 30)}): {_untrusted(e.get('claim'), 500)}"
        for e in analysis.get("evidence") or []
        if isinstance(e, dict)
    ]
    uncertainty_lines = [f"- {_untrusted(u, 500)}" for u in analysis.get("uncertainties") or []]
    test_lines = [f"- [ ] {_untrusted(t, 300)}" for t in analysis.get("tests_to_run") or []]
    check_rows = [
        f"| {name} | {c.get('score', 0):.2f} / {c.get('max', 0):.2f} |"
        for name, c in (evaluation.get("checks") or {}).items()
    ]
    approval_line = f"- **Approved for PR creation by:** {_untrusted(reviewer, 200)}" if reviewer else ""
    return "\n".join(
        [
            "## Proposed fix from OpsPulse AI - requires human review",
            "",
            "> This change was generated by an LLM and checked only by automated *structural* checks (schema, ",
            "> evidence quotes, patch applies to the retrieved source). " + _tests_notice(analysis.get("sandbox")),
            "> Review the reasoning, run the test suite and decide whether to merge.",
            "",
            f"- **Incident ID:** `{fix.incident_id}`",
            _revision_line(analysis),
            f"- **Title:** {_untrusted(summary.get('title'), 200)}",
            f"- **Root-cause category (model-assigned):** {_untrusted(analysis.get('root_cause_category'), 40)}",
            f"- **Trigger frame:** `{_untrusted(summary.get('trigger_frame'), 200)}`",
            f"- **Quality-gate score:** {fix.quality_score:.2f} after {fix.iterations} attempt(s) "
            "(rubric score, not a probability of correctness)",
            approval_line,
            "",
            "### Root cause (hypothesis)",
            _untrusted(root.get("technical_explanation")),
            "",
            f"**Symptom vs cause:** {_untrusted(root.get('symptom_vs_cause'))}",
            "",
            "### Evidence",
            *(evidence_lines or ["- none provided"]),
            "",
            "### Proposed change",
            _untrusted(remediation.get("explanation")),
            "",
            f"**Possible side effects:** {_untrusted(remediation.get('side_effects'))}",
            "",
            "### Uncertainties",
            *(uncertainty_lines or ["- none stated"]),
            "",
            "### Suggested tests (not run)",
            *(test_lines or ["- none suggested"]),
            "",
            "### Automated checks",
            "| Check | Score |",
            "|---|---|",
            *check_rows,
        ]
    )


def _preconditions(fix: ProposedFix) -> RemediationOutcome | None:
    """Configuration and patch policy. Returns an outcome when remediation must not proceed."""
    if not settings.enable_github_remediation:
        return RemediationOutcome(
            "analysis_ready",
            "GitHub remediation disabled (ENABLE_GITHUB_REMEDIATION=false)",
            ErrorCategory.REMEDIATION_SKIPPED,
        )
    if not settings.github_configured:
        return RemediationOutcome(
            "analysis_ready",
            "GitHub remediation skipped: GITHUB_TOKEN not configured",
            ErrorCategory.REMEDIATION_SKIPPED,
        )
    sandbox = fix.analysis.get("sandbox")
    if isinstance(sandbox, dict) and sandbox.get("status") == "regression":
        return RemediationOutcome(
            "needs_review",
            f"the patch breaks tests that passed before it: {_untrusted(sandbox.get('summary'), 200)}",
            ErrorCategory.REMEDIATION_POLICY_VIOLATION,
        )
    try:
        validate_patch_for_pr(fix.patch, fix.target_file, settings.max_patch_changed_lines)
    except PatchRejectedError as exc:
        return RemediationOutcome(
            "needs_review", f"patch rejected by remediation policy: {exc}", ErrorCategory.REMEDIATION_POLICY_VIOLATION
        )
    return None


def _open_pull_request(fix: ProposedFix, reviewer: str | None) -> RemediationOutcome:
    existing = repositories.find_recent_pr(fix.repo_name, fix.fingerprint)
    if existing:
        return RemediationOutcome(
            "pr_skipped_duplicate", "a PR for this failure was opened in the last 24h", pr_url=existing
        )
    with observe("github.create_pull_request", as_type="tool") as span:
        try:
            pr = create_fix_pull_request(
                repo_name=fix.repo_name,
                file_path=fix.target_file or "",
                unified_diff=fix.patch or "",
                title=build_pull_request_title(fix.analysis),
                body=build_pull_request_body(fix, reviewer),
                fingerprint=fix.fingerprint,
            )
        except GitHubError as exc:
            logger.warning("PR creation failed: %s", exc)
            span.update(error=exc.category.value)
            return RemediationOutcome("pr_failed", f"PR creation failed: {exc}", exc.category)
        span.update(metadata={"already_existed": pr.already_existed})
    if pr.already_existed:
        return RemediationOutcome(
            "pr_skipped_duplicate",
            "an open PR already exists for this failure",
            None,
            pr.pr_url,
            pr.pr_number,
            pr.branch,
        )
    return RemediationOutcome(
        "pr_created",
        "PR opened for human review; it is never merged automatically",
        None,
        pr.pr_url,
        pr.pr_number,
        pr.branch,
    )


def remediate(fix: ProposedFix) -> RemediationOutcome:
    """Called once after an accepted analysis. Raises SQLAlchemyError if the DB is unavailable."""
    blocked = _preconditions(fix)
    if blocked:
        return blocked
    if settings.require_remediation_approval:
        approval = repositories.create_approval(
            fix.incident_id, fix.target_file or "", patch_sha256(fix.patch), settings.approval_ttl_hours
        )
        return RemediationOutcome(
            "awaiting_approval", "proposed PR is waiting for an explicit human decision", approval=approval
        )
    return _open_pull_request(fix, reviewer=None)


def _expired(approval: dict[str, Any]) -> bool:
    return datetime.fromisoformat(approval["expires_at"]) <= datetime.now(UTC)


def decide(
    fix: ProposedFix, approval_id: uuid.UUID, claimed_patch_sha256: str, approve: bool, reviewer: str, note: str | None
) -> RemediationOutcome:
    """Apply a human decision to a pending approval. Raises ApprovalError subclasses for invalid requests."""
    approval = repositories.get_approval(approval_id)
    if approval is None or approval["incident_id"] != str(fix.incident_id):
        raise ApprovalNotFoundError("approval not found for this incident")
    if approval["status"] != "pending":
        raise ApprovalConflictError(f"approval is already {approval['status']}")
    if _expired(approval):
        repositories.transition_approval(approval_id, "pending", "expired")
        raise ApprovalConflictError("approval has expired; re-submit the incident to get a new proposal")
    current_hash = patch_sha256(fix.patch)
    if claimed_patch_sha256 != approval["patch_sha256"] or current_hash != approval["patch_sha256"]:
        raise ApprovalConflictError("patch_sha256 does not match the proposed patch (stale or wrong approval)")

    if not approve:
        if not repositories.transition_approval(approval_id, "pending", "rejected", reviewer, note):
            raise ApprovalConflictError("approval was decided concurrently")
        return RemediationOutcome(
            "remediation_rejected", f"remediation rejected by {reviewer}", ErrorCategory.APPROVAL_REJECTED
        )

    # Claim the approval atomically BEFORE the side effect: a concurrent or repeated approve gets a conflict.
    if not repositories.transition_approval(approval_id, "pending", "approved", reviewer, note):
        raise ApprovalConflictError("approval was decided concurrently")
    blocked = _preconditions(fix)  # configuration may have changed since the proposal
    outcome = blocked or _open_pull_request(fix, reviewer)
    final = "executed" if outcome.status in ("pr_created", "pr_skipped_duplicate") else "execution_failed"
    repositories.transition_approval(approval_id, "approved", final)
    return outcome
