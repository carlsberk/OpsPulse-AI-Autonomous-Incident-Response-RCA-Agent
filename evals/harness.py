"""Runs the real LangGraph workflow on one evaluation case with local (non-GitHub) source and history.

Only the model differs between modes:
- mock: scripted replies from the dataset (deterministic; measures the evaluator and the harness, NOT a model)
- live: the configured Groq model (requires GROQ_API_KEY)
"""

from __future__ import annotations

import difflib
import time
import uuid
from collections.abc import Callable
from typing import Any, cast

from langchain_core.messages import AIMessage

from evals.dataset import EvalCase
from src.agent.evaluation import EvaluationInput, verify_affected_files, verify_evidence
from src.agent.graph import build_graph
from src.agent.parsing import compute_fingerprint, get_trigger_frame, normalize_path, path_candidates
from src.agent.patching import format_code_window
from src.agent.state import IncidentState, initial_state
from src.config import settings
from src.integrations.github import SourceContext
from src.integrations.llm import ModelFactory
from src.integrations.observability import build_run_config
from src.retrieval.history import HistoryCandidate, HistoryQuery, rank_lexical, to_prompt_records

# ------------------------------------------------------------------ local retrieval


def local_source_fetcher(case: EvalCase) -> Callable[[str, str, int], SourceContext | None]:
    def fetch(repo_name: str, runtime_path: str, failing_line: int) -> SourceContext | None:
        for candidate in path_candidates(runtime_path):
            match = next((p for p in case.files if p == candidate or p.endswith("/" + candidate)), None)
            if match:
                lines = case.files[match].splitlines()
                line = min(max(failing_line, 1), len(lines))
                start, end = max(1, line - settings.context_radius), min(len(lines), line + settings.context_radius)
                window = format_code_window(lines[start - 1 : end], start)
                return SourceContext(match, "eval", start, end, line, window, [])
        return None

    return fetch


def local_history(case: EvalCase) -> Callable[[HistoryQuery, str], list[dict[str, Any]]]:
    candidates = [
        HistoryCandidate(
            id=f"{case.id}-h{i}",
            repo_name=case.repo_name,
            fingerprint=compute_fingerprint(case.repo_name, h.error_message, h.stack_trace),
            error_message=h.error_message,
            stack_trace=h.stack_trace,
            affected_file=h.affected_file,
            extra={"root_cause_summary": h.root_cause_summary, "status": h.status, "suggested_patch": ""},
        )
        for i, h in enumerate(case.history)
    ]

    def lookup(query: HistoryQuery, exclude_id: str) -> list[dict[str, Any]]:
        return to_prompt_records(rank_lexical(query, candidates))

    return lookup


# ------------------------------------------------------------------ scripted model


def _diff(path: str, original: str, fix: dict[str, str], corrupt: bool) -> str:
    if fix["old"] not in original:
        raise ValueError(f"fix.old not found in {path}")
    patched = original.replace(fix["old"], fix["new"], 1)
    lines = list(
        difflib.unified_diff(
            original.splitlines(keepends=True), patched.splitlines(keepends=True), f"a/{path}", f"b/{path}", n=3
        )
    )
    if corrupt:  # simulate a model that paraphrases a context line instead of copying it
        for i, line in enumerate(lines):
            if line.startswith(" ") and line.strip():
                lines[i] = " # paraphrased context line\n"
                break
    return "".join(lines)


def build_scripted_reply(case: EvalCase, spec: dict[str, Any]) -> str:
    """Expand a compact mock-reply spec from the dataset into a full RCA JSON reply."""
    import json

    if "raw" in spec:
        return str(spec["raw"])
    frame = get_trigger_frame(case.stack_trace)
    target = normalize_path(frame.path) if frame else None
    trigger = spec.get("trigger") or (f"{target}:{frame.line} -> {frame.function}()" if frame else "unknown")
    evidence = []
    if spec.get("quote"):
        evidence.append(
            {
                "kind": "observed",
                "claim": "the failing statement",
                "quote": spec["quote"],
                "source": spec.get("quote_source", "code_context"),
            }
        )
    last_line = next((ln.strip() for ln in reversed(case.stack_trace.splitlines()) if ln.strip()), "")
    if last_line:
        evidence.append(
            {"kind": "observed", "claim": "the raised exception", "quote": last_line[:200], "source": "stack_trace"}
        )
    evidence.append({"kind": "inference", "claim": "the input violated an assumption", "quote": "", "source": "none"})
    fix = spec.get("fix")
    diff = (
        _diff(target, case.files[target], fix, spec.get("corrupt_diff", False)) if fix and target in case.files else ""
    )
    if diff and spec.get("retarget"):  # adversarial: same hunks, but the headers claim another file
        diff = diff.replace(f"a/{target}", f"a/{spec['retarget']}").replace(f"b/{target}", f"b/{spec['retarget']}")
    sufficient = spec.get("sufficient", True)
    return json.dumps(
        {
            "incident_summary": {
                "title": case.scenario[:120],
                "severity": "HIGH",
                "failing_service": case.repo_name,
                "trigger_frame": trigger,
            },
            "root_cause_category": spec["category"],
            "hypotheses": [
                {
                    "id": "H1",
                    "description": f"{spec['category']} in the trigger frame",
                    "evidence": "see evidence",
                    "likelihood": 0.7,
                },
                {
                    "id": "H2",
                    "description": "an upstream caller supplied bad input",
                    "evidence": "not ruled out",
                    "likelihood": 0.2,
                },
            ],
            "evidence": evidence,
            "affected_files": spec.get("files") or ([target] if target else []),
            "diagnostic_chain": {
                "primary_root_cause": {
                    "hypothesis_id": "H1",
                    "technical_explanation": f"scripted {spec['category']} explanation",
                    "symptom_vs_cause": "the exception is the symptom",
                    "justification": "scripted",
                }
            },
            "patch_remediation": {
                "explanation": "scripted fix" if diff else "no safe code change identified",
                "side_effects": "none known",
                "unified_diff": diff,
            },
            "uncertainties": ["scripted reply"],
            "tests_to_run": ["reproduce the failing call"],
            "control_flow": {
                "self_assessed_confidence": spec.get("confidence", 0.75),
                "needs_human_review": not sufficient,
                "evidence_sufficient": sufficient,
            },
        }
    )


class ScriptedModel:
    """Chat-model stand-in: returns the case's scripted replies in order (the last one repeats)."""

    def __init__(self, case: EvalCase) -> None:
        self.replies = [build_scripted_reply(case, spec) for spec in case.mock_replies]
        self.calls = 0

    def invoke(self, messages: Any, config: Any = None) -> AIMessage:
        reply = self.replies[min(self.calls, len(self.replies) - 1)]
        self.calls += 1
        return AIMessage(content=reply)


# ------------------------------------------------------------------ run one case


def run_final(case: EvalCase, model_factory: ModelFactory | None) -> tuple[IncidentState, int]:
    """Run the graph for one case; returns (final state, wall milliseconds)."""
    graph = build_graph(local_source_fetcher(case), local_history(case), model_factory)
    incident_id = str(uuid.uuid5(uuid.NAMESPACE_URL, f"opspulse-eval/{case.id}"))
    started = time.perf_counter()
    final = cast(
        IncidentState,
        graph.invoke(
            initial_state(incident_id, case.error_message, case.stack_trace, case.repo_name),
            config=build_run_config(incident_id, case.repo_name, "eval"),
        ),
    )
    return final, int((time.perf_counter() - started) * 1000)


_STATUS_FOR = {"accepted": "analysis_ready", "needs_review": "needs_review", "failed": "failed"}


def incident_record(case: EvalCase, final: IncidentState) -> dict[str, Any]:
    """The incident record shape the API serves, built from a final graph state (no database, no remediation)."""
    analysis = dict(final["root_cause_analysis"] or {})
    analysis["attempts"] = final["attempts"]
    return {
        "incident_id": str(uuid.uuid5(uuid.NAMESPACE_URL, f"opspulse-eval/{case.id}")),
        "repo_name": case.repo_name,
        "status": _STATUS_FOR.get(final["workflow_status"], final["workflow_status"]),
        "status_reason": None,
        "error_category": final["error_category"],
        "error_message": case.error_message,
        "affected_file": final["affected_file"],
        "quality_score": final["quality_score"],
        "iterations": final["iterations"],
        "analysis": analysis,
        "suggested_patch": final["suggested_patch"],
    }


def run_case(case: EvalCase, model_factory: ModelFactory | None) -> dict[str, Any]:
    """Execute the workflow and return the raw facts the metrics are computed from."""
    final, wall_ms = run_final(case, model_factory)
    analysis = {
        k: v for k, v in (final["root_cause_analysis"] or {}).items() if k not in ("execution_metadata", "evaluation")
    }
    inp = EvaluationInput(
        analysis=analysis,
        patch=final["suggested_patch"],
        stack_trace=final["stack_trace"],
        trigger=get_trigger_frame(final["stack_trace"]),
        affected_file=final["affected_file"],
        code_context=final["code_context"],
        caller_context=final.get("caller_context"),
        historical_matches=final["historical_matches"],
        max_changed_lines=settings.max_patch_changed_lines,
        error_message=final["error_message"],
    )
    evidence = verify_evidence(inp)
    files = verify_affected_files(inp)
    raw_control = analysis.get("control_flow")
    control: dict[str, Any] = raw_control if isinstance(raw_control, dict) else {}
    return {
        "case_id": case.id,
        "expected": case.expected.model_dump(),
        "workflow_status": final["workflow_status"],
        "error_category": final["error_category"],
        "error": final.get("error"),  # our own message (e.g. which provider limit), never provider text
        "gate_passed": final["quality_gate_passed"],
        "quality_score": final["quality_score"],
        "predicted_category": analysis.get("root_cause_category"),
        "declared_insufficient": control.get("evidence_sufficient") is False,
        "model_confidence": control.get("self_assessed_confidence"),
        "observed_quotes": len(evidence),
        "grounded_quotes": sum(1 for e in evidence if e is None),
        "affected_files": sorted(files),
        "grounded_files": sum(files.values()),
        "attempts": final["attempts"],
        # For diagnosis of failures (the cases are synthetic, so storing the patch and check texts is safe).
        "patch": final["suggested_patch"],
        "failed_check_details": {
            name: str(check.get("detail"))[:600]
            for name, check in (
                ((final["root_cause_analysis"] or {}).get("evaluation") or {}).get("checks") or {}
            ).items()
            if isinstance(check, dict) and (check.get("score") or 0) < (check.get("max") or 0) - 1e-9
        },
        "wall_ms": wall_ms,
    }
