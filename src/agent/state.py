"""The single, authoritative state contract shared by every LangGraph node.

Semantics
---------
- ``iterations``: number of *completed* analysis attempts (0 before the first LLM call).
  ``analyze_root_cause`` increments it; routing allows another attempt only while
  ``iterations < settings.max_analysis_iterations``.
- ``quality_score``: weighted score of deterministic checks (0..1). It is a rubric score,
  NOT a calibrated probability that the root cause is correct.
- ``workflow_status``:
    running      - graph is still executing (also: evaluator requested another attempt)
    accepted     - the latest analysis passed the quality gate
    needs_review - attempts exhausted, evidence insufficient, or no source to ground a retry
    failed       - an unrecoverable error (e.g. LLM provider unavailable); see ``error``
- ``error_category``: why the workflow failed or stopped short of acceptance (src/errors.py).
- ``context_status`` / ``history_status``: what retrieval actually produced, so the prompt,
  evaluator and API never pretend that unavailable evidence was inspected.
"""

from __future__ import annotations

from typing import Any, Literal, TypedDict

WorkflowStatus = Literal["running", "accepted", "needs_review", "failed"]
ContextStatus = Literal["pending", "retrieved", "no_frame", "not_found", "unavailable"]
HistoryStatus = Literal["pending", "ok", "unavailable"]


class IncidentState(TypedDict):
    incident_id: str
    error_message: str
    stack_trace: str
    repo_name: str
    commit_sha: str | None  # deployed commit reported with the incident; source is read at it when set
    affected_file: str | None
    failing_line: int | None
    code_context: str | None
    caller_context: str | None  # short windows around calling application frames (diagnosis only, never patched)
    context_status: ContextStatus
    context_note: str | None
    historical_matches: list[dict[str, Any]]
    history_status: HistoryStatus
    root_cause_analysis: dict[str, Any] | None
    suggested_patch: str | None
    quality_score: float
    quality_gate_passed: bool
    iterations: int
    previous_feedback: str | None
    workflow_status: WorkflowStatus
    error: str | None
    error_category: str | None  # src/errors.py ErrorCategory value
    attempts: list[dict[str, Any]]  # per LLM attempt: latency, provider token usage, output validity


def initial_state(
    incident_id: str, error_message: str, stack_trace: str, repo_name: str, commit_sha: str | None = None
) -> IncidentState:
    """Build the entry state for the graph."""
    return IncidentState(
        incident_id=incident_id,
        error_message=error_message,
        stack_trace=stack_trace,
        repo_name=repo_name,
        commit_sha=commit_sha,
        affected_file=None,
        failing_line=None,
        code_context=None,
        caller_context=None,
        context_status="pending",
        context_note=None,
        historical_matches=[],
        history_status="pending",
        root_cause_analysis=None,
        suggested_patch=None,
        quality_score=0.0,
        quality_gate_passed=False,
        iterations=0,
        previous_feedback=None,
        workflow_status="running",
        error=None,
        error_category=None,
        attempts=[],
    )
