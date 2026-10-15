"""Operational failure taxonomy, persisted in `incidents.error_category` and exposed by the API.

A *failed* workflow (status `failed`) produced no usable analysis. A *partial* result (e.g. `needs_review`,
`analysis_ready`, `pr_failed`) has a stored analysis but a later stage was skipped or did not succeed; the
category says why.
"""

from __future__ import annotations

from enum import StrEnum


class ErrorCategory(StrEnum):
    # workflow failures (no usable analysis)
    LLM_NOT_CONFIGURED = "llm_not_configured"
    LLM_UNAVAILABLE = "llm_unavailable"
    LLM_RATE_LIMITED = "llm_rate_limited"
    LLM_REQUEST_TOO_LARGE = "llm_request_too_large"  # one request exceeds a per-minute cap: waiting cannot help
    LLM_TIMEOUT = "llm_timeout"
    LLM_AUTH = "llm_auth"
    LLM_BAD_REQUEST = "llm_bad_request"
    DATABASE_UNAVAILABLE = "database_unavailable"
    INTERNAL_ERROR = "internal_error"
    INTERRUPTED = "interrupted"  # workers stopped mid-analysis MAX_JOB_ATTEMPTS times
    # partial results (analysis stored, a later stage did not happen)
    MALFORMED_MODEL_OUTPUT = "malformed_model_output"
    RETRY_BUDGET_EXHAUSTED = "retry_budget_exhausted"
    INSUFFICIENT_EVIDENCE = "insufficient_evidence"
    NO_CODE_FIX = "no_code_fix"  # grounded analysis, but the cause is operational (config, dependency, infra)
    SOURCE_UNAVAILABLE = "source_unavailable"
    RETRIEVAL_FAILED = "retrieval_failed"
    REMEDIATION_SKIPPED = "remediation_skipped"
    REMEDIATION_POLICY_VIOLATION = "remediation_policy_violation"
    APPROVAL_REJECTED = "approval_rejected"
    APPROVAL_EXPIRED = "approval_expired"
    GITHUB_PERMISSION = "github_permission"
    GITHUB_RATE_LIMITED = "github_rate_limited"
    GITHUB_UNAVAILABLE = "github_unavailable"


# Provider failures that may succeed later; the queue retries them with backoff instead of failing the incident.
TRANSIENT_CATEGORIES = frozenset({"llm_rate_limited", "llm_timeout", "llm_unavailable"})

LLM_CATEGORY = {
    "not_configured": ErrorCategory.LLM_NOT_CONFIGURED,
    "model_unavailable": ErrorCategory.LLM_UNAVAILABLE,
    "connection": ErrorCategory.LLM_UNAVAILABLE,
    "provider_error": ErrorCategory.LLM_UNAVAILABLE,
    "rate_limited": ErrorCategory.LLM_RATE_LIMITED,
    "request_too_large": ErrorCategory.LLM_REQUEST_TOO_LARGE,
    "timeout": ErrorCategory.LLM_TIMEOUT,
    "auth": ErrorCategory.LLM_AUTH,
    "bad_request": ErrorCategory.LLM_BAD_REQUEST,
}
