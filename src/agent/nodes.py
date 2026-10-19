"""LangGraph nodes. Each node reads/writes `IncidentState` fields only and performs no persistence or
GitHub write side effects - those happen once, after the graph finishes (src/services/)."""

from __future__ import annotations

import hashlib
import json
import logging
import re
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_core.runnables import RunnableConfig
from pydantic import ValidationError
from sqlalchemy.exc import SQLAlchemyError

from src import metrics
from src.agent.evaluation import Evaluation, EvaluationInput, evaluate
from src.agent.parsing import (
    clean_text,
    compute_fingerprint,
    get_application_frames,
    get_trigger_frame,
    normalize_path,
    split_embedded_trace,
)
from src.agent.patching import PatchError, format_code_window, parse_code_window
from src.agent.prompts import RCA_SYSTEM_PROMPT, build_rca_user_prompt
from src.agent.schemas import RCAOutput
from src.agent.state import IncidentState
from src.config import settings
from src.errors import LLM_CATEGORY, ErrorCategory
from src.integrations.github import TRANSPORT_ERRORS, GitHubError, SourceContext
from src.integrations.llm import LLMError, ModelFactory, invoke_json_model
from src.integrations.observability import observe
from src.logging_config import redact
from src.retrieval.history import HistoryQuery
from src.versions import run_metadata

logger = logging.getLogger(__name__)

ContextFetcher = Callable[[str, str, int], SourceContext | None]  # (repo, path, failing_line)
HistoryLookup = Callable[[HistoryQuery, str], list[dict[str, Any]]]  # (query, incident id to exclude)

Update = dict[str, Any]


# ------------------------------------------------------------------ ingestion


def normalize_incident(state: IncidentState) -> Update:
    """Strip ANSI/NUL characters, cap sizes, and separate a traceback embedded in the message."""
    message, trace = split_embedded_trace(state["error_message"] or "", state["stack_trace"] or "")
    # Credentials in reported errors are masked before they are stored, sent to the model or quoted anywhere.
    return {
        "error_message": redact(clean_text(message, 10_000)) or "unknown error",
        "stack_trace": redact(clean_text(trace, 60_000)),
    }


def extract_stack_trace_context(state: IncidentState) -> Update:
    """Locate the trigger frame: the application (non-library) frame closest to the crash."""
    frame = get_trigger_frame(state["stack_trace"])
    if frame is None:
        logger.info("no application frame found in stack trace")
        return {"affected_file": None, "failing_line": None}
    affected = normalize_path(frame.path)
    logger.info("trigger frame %s:%d", affected, frame.line)
    return {"affected_file": affected, "failing_line": frame.line}


def make_retrieve_source_context(fetch: ContextFetcher) -> Callable[[IncidentState], Update]:
    def retrieve_source_context(state: IncidentState) -> Update:
        """Fetch a numbered source window. Any failure degrades to 'no context', never to invented context."""
        if not state["affected_file"] or state["failing_line"] is None:
            return {"context_status": "no_frame", "context_note": "no application frame in the stack trace"}
        with observe("retrieval.source_context", as_type="retriever") as span:
            try:
                ctx = fetch(state["repo_name"], state["affected_file"], state["failing_line"])
            except GitHubError as exc:
                logger.warning("source retrieval unavailable: %s", exc)
                span.update(error=str(exc))
                return {"context_status": "unavailable", "context_note": str(exc)}
            except TRANSPORT_ERRORS as exc:
                note = f"GitHub request failed ({type(exc).__name__})"
                logger.warning("source retrieval failed: %s", type(exc).__name__)
                span.update(error=note)
                return {"context_status": "unavailable", "context_note": note}
            span.update(metadata={"found": ctx is not None})
        if ctx is None:
            return {"context_status": "not_found", "context_note": "file not found in the repository default branch"}
        return {
            "context_status": "retrieved",
            "context_note": None,
            "affected_file": ctx.path,
            "code_context": ctx.render(),
            "caller_context": _caller_context(fetch, state, ctx),
        }

    return retrieve_source_context


def _caller_context(fetch: ContextFetcher, state: IncidentState, trigger_ctx: SourceContext) -> str | None:
    """Short windows around the calling application frames, so a cause in a caller is visible.

    Best effort: a frame that cannot be fetched is skipped, and lines already shown are not repeated. Only the
    trigger file may be patched; callers are context for the diagnosis.
    """
    if settings.caller_frames <= 0:
        return None
    blocks: list[str] = []
    seen = {(trigger_ctx.path, line) for line in range(trigger_ctx.start_line, trigger_ctx.end_line + 1)}
    for frame in get_application_frames(state["stack_trace"])[1:]:
        if len(blocks) >= settings.caller_frames:
            break
        try:
            ctx = fetch(state["repo_name"], normalize_path(frame.path), frame.line)
        except (GitHubError, *TRANSPORT_ERRORS) as exc:
            logger.info("caller context skipped: %s", type(exc).__name__)
            continue
        if ctx is None or (ctx.path, ctx.failing_line) in seen:
            continue
        try:
            first, lines = parse_code_window(ctx.window_text)
        except PatchError:
            continue
        lo = max(ctx.failing_line - settings.caller_radius, first)
        hi = min(ctx.failing_line + settings.caller_radius, first + len(lines) - 1)
        seen.update((ctx.path, line) for line in range(lo, hi + 1))
        blocks.append(
            f"FILE: {ctx.path} (caller {frame.function or '?'}, line {ctx.failing_line})\n"
            + format_code_window(lines[lo - first : hi - first + 1], lo)
        )
    return "\n\n".join(blocks) or None


def make_retrieve_historical_incidents(lookup: HistoryLookup) -> Callable[[IncidentState], Update]:
    def retrieve_historical_incidents(state: IncidentState) -> Update:
        """Ranked same-repository incidents (src/retrieval/history.py). A DB outage only disables history."""
        query = HistoryQuery(
            repo_name=state["repo_name"],
            fingerprint=compute_fingerprint(state["repo_name"], state["error_message"], state["stack_trace"]),
            error_message=state["error_message"],
            stack_trace=state["stack_trace"],
            affected_file=state["affected_file"],
        )
        with observe("retrieval.historical_incidents", as_type="retriever") as span:
            try:
                matches = lookup(query, state["incident_id"])
            except SQLAlchemyError as exc:
                logger.warning("history lookup unavailable: %s", type(exc).__name__)
                span.update(error=ErrorCategory.RETRIEVAL_FAILED.value)
                return {"historical_matches": [], "history_status": "unavailable"}
            span.update(metadata={"matches": len(matches)})
        logger.info("history lookup: %d match(es)", len(matches))
        return {"historical_matches": matches, "history_status": "ok"}

    return retrieve_historical_incidents


# ------------------------------------------------------------------ analysis


def extract_json_object(text: str) -> dict[str, Any] | None:
    """Parse the model reply into a dict, tolerating stray code fences or prose around one object."""
    if not text:
        return None
    stripped = text.strip()
    candidates = [stripped, re.sub(r"^```(?:json)?\s*|\s*```$", "", stripped, flags=re.IGNORECASE)]
    first, last = stripped.find("{"), stripped.rfind("}")
    if first != -1 and last > first:
        candidates.append(stripped[first : last + 1])
    for candidate in candidates:
        try:
            value = json.loads(candidate)
        except ValueError:
            continue
        if isinstance(value, dict):
            return value
    return None


def _digest(analysis: dict[str, Any]) -> str:
    return hashlib.sha256(json.dumps(analysis, sort_keys=True, ensure_ascii=False, default=str).encode()).hexdigest()


def make_analyze_root_cause(
    model_factory: ModelFactory | None = None,
) -> Callable[[IncidentState, RunnableConfig], Update]:
    """`model_factory=None` uses the configured Groq model; evaluations and tests inject their own."""

    def analyze_root_cause(state: IncidentState, config: RunnableConfig) -> Update:
        return _analyze_root_cause(state, config, model_factory)

    return analyze_root_cause


def _analyze_root_cause(state: IncidentState, config: RunnableConfig, model_factory: ModelFactory | None) -> Update:
    """One LLM attempt. Validates/normalizes the reply; provider failures end the workflow as `failed`."""
    iteration = state["iterations"] + 1
    previous = state["root_cause_analysis"] if iteration > 1 else None
    messages = [
        SystemMessage(content=RCA_SYSTEM_PROMPT),
        HumanMessage(content=build_rca_user_prompt(dict(state), previous)),
    ]
    try:
        reply = invoke_json_model(messages, 0.0 if iteration == 1 else 0.2, config, model_factory)
    except LLMError as exc:
        category = LLM_CATEGORY.get(exc.category, ErrorCategory.LLM_UNAVAILABLE)
        logger.error("LLM call failed: category=%s", category.value)
        failed_attempt = {"iteration": iteration, "error_category": category.value}
        metrics.record_attempt(failed_attempt)
        return {
            "iterations": iteration,
            "workflow_status": "failed",
            "error": str(exc),
            "error_category": category.value,
            "attempts": [*state["attempts"], failed_attempt],
        }

    analysis = extract_json_object(reply.text)
    schema_valid = False
    schema_error_fields: list[str] = []
    if analysis is None:
        analysis = {
            "output_error": "the reply was cut off at the output-token limit; answer more concisely"
            if reply.truncated
            else "the model did not return a valid JSON object"
        }
    else:
        try:
            analysis = RCAOutput.model_validate(analysis).model_dump()
            schema_valid = True
        except ValidationError as exc:
            # Keep the raw object: the evaluator turns each schema error into feedback for the next attempt.
            logger.info("analysis reply has %d schema error(s)", exc.error_count())
            # Field paths and error types only (no content) - diagnosable from metrics and reports.
            schema_error_fields = [f"{'.'.join(map(str, e['loc']))}:{e['type']}" for e in exc.errors()[:8]]
    remediation = analysis.get("patch_remediation")
    patch = remediation.get("unified_diff") if isinstance(remediation, dict) else None
    patch = patch if isinstance(patch, str) and patch.strip() else None

    attempt = {
        "iteration": iteration,
        "latency_ms": reply.latency_ms,
        "input_tokens": reply.input_tokens,
        "output_tokens": reply.output_tokens,
        "truncated": reply.truncated,
        "valid_json": "output_error" not in analysis,
        "schema_valid": schema_valid,
        "schema_error_fields": schema_error_fields,
    }
    metrics.record_attempt(attempt)
    analysis["execution_metadata"] = {  # produced in code, never by the LLM
        **run_metadata(),
        "iteration": iteration,
        "timestamp_iso": datetime.now(UTC).isoformat(),
        "analysis_sha256": _digest(analysis),
    }
    logger.info(
        "analysis attempt %d: valid_json=%s schema_valid=%s patch=%s",
        iteration,
        attempt["valid_json"],
        schema_valid,
        bool(patch),
    )
    return {
        "root_cause_analysis": analysis,
        "suggested_patch": patch,
        "iterations": iteration,
        "attempts": [*state["attempts"], attempt],
    }


def _llm_seconds(state: IncidentState) -> float:
    """Time the model calls of this incident have taken so far (recorded per attempt)."""
    return sum((a.get("latency_ms") or 0) for a in state["attempts"]) / 1000


def evaluate_analysis(state: IncidentState) -> Update:
    """Run the deterministic quality gate and decide the next workflow status."""
    analysis = dict(state["root_cause_analysis"] or {})
    content = {k: v for k, v in analysis.items() if k not in ("execution_metadata", "evaluation")}
    with observe("evaluation.quality_gate", as_type="evaluator") as span:
        result = _evaluate(state, content)
        metrics.record_gate(result.passed)
        span.update(metadata={"score": result.score, "passed": result.passed, "attempt": state["iterations"]})
    category: ErrorCategory | None = None
    if result.passed:
        status, reason = "accepted", "quality gate passed"
    elif not result.evidence_sufficient:
        status, reason = "needs_review", "model reported insufficient evidence"
        category = ErrorCategory.INSUFFICIENT_EVIDENCE
    elif result.grounded_without_patch(state["suggested_patch"]):
        # Retrying cannot help: the analysis is grounded and the model says no code change is appropriate.
        status, reason = "needs_review", "grounded analysis without a code patch (operational root cause?)"
        category = ErrorCategory.NO_CODE_FIX
    elif state["iterations"] >= settings.max_analysis_iterations:
        status, reason = "needs_review", "analysis attempt budget exhausted"
        malformed = "output_error" in content
        category = ErrorCategory.MALFORMED_MODEL_OUTPUT if malformed else ErrorCategory.RETRY_BUDGET_EXHAUSTED
    elif _llm_seconds(state) >= settings.max_incident_seconds:
        status, reason = "needs_review", "analysis time budget exhausted"
        category = ErrorCategory.TIME_BUDGET_EXHAUSTED
    elif not state["code_context"]:
        status, reason = "needs_review", "source code unavailable; another attempt cannot add grounding"
        category = ErrorCategory.SOURCE_UNAVAILABLE
    else:
        status, reason = "running", "retrying with evaluator feedback"
    analysis["evaluation"] = {**result.as_dict(), "attempt": state["iterations"], "decision": reason}
    # Per-attempt trace of the gate's verdict (names of failed checks) for review and analysis.
    attempts = list(state["attempts"])
    if attempts:
        failed = [name for name, check in result.checks.items() if check.fraction < 1.0]
        record = {
            **attempts[-1],
            "gate_score": result.score,
            "gate_passed": result.passed,
            "failed_checks": failed,
            "decision": reason,
        }
        attempts[-1] = record
    logger.info(
        "evaluation attempt %d: score=%.4f passed=%s -> %s", state["iterations"], result.score, result.passed, status
    )
    return {
        "quality_score": result.score,
        "quality_gate_passed": result.passed,
        "previous_feedback": result.feedback,
        "root_cause_analysis": analysis,
        "workflow_status": status,
        "error_category": category.value if category else None,
        "attempts": attempts,
    }


def _evaluate(state: IncidentState, content: dict[str, Any]) -> Evaluation:
    return evaluate(
        EvaluationInput(
            analysis=content,
            patch=state["suggested_patch"],
            stack_trace=state["stack_trace"],
            trigger=get_trigger_frame(state["stack_trace"]),
            affected_file=state["affected_file"],
            code_context=state["code_context"],
            caller_context=state.get("caller_context"),
            historical_matches=state["historical_matches"],
            max_changed_lines=settings.max_patch_changed_lines,
            error_message=state["error_message"],
        ),
        threshold=settings.quality_threshold,
    )
