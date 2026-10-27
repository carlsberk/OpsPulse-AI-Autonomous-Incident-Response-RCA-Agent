"""Deterministic quality gate for an RCA attempt. Pure functions, no I/O, no LLM call.

The gate answers "is this analysis well-formed, grounded in the data we actually retrieved, and is its
patch mechanically applicable near the failure?" It CANNOT answer "is the fix functionally correct?" -
only tests and a human reviewer can. See README "Evaluation limitations".
"""

from __future__ import annotations

import ast
import re
from collections import Counter
from dataclasses import dataclass
from typing import Any

from pydantic import ValidationError

from src.agent.parsing import Frame
from src.agent.patching import PatchError, apply_hunks, parse_code_window, parse_unified_diff, path_matches
from src.agent.prompts import format_history
from src.agent.schemas import RCAOutput

# Weights sum to 1.0. Blocking checks must score 1.0 for the gate to pass, regardless of the total.
CHECK_WEIGHTS: dict[str, float] = {
    "schema": 0.10,
    "trigger_grounding": 0.10,
    "evidence_grounding": 0.15,
    "affected_files_grounding": 0.05,
    "diff_wellformed": 0.10,
    "diff_applies": 0.20,
    "fix_location": 0.05,
    "patch_minimal": 0.05,
    "patch_locality": 0.05,
    "trace_code_consistency": 0.05,
    "self_assessment": 0.05,
    "patch_effective": 0.05,
}
BLOCKING_CHECKS = frozenset(
    {
        "schema",
        "trigger_grounding",
        "evidence_grounding",
        "trace_code_consistency",
        "diff_wellformed",
        "diff_applies",
        "patch_effective",
    }
)
LOCALITY_RADIUS = 30
MIN_QUOTE_CHARS = 4

if abs(sum(CHECK_WEIGHTS.values()) - 1.0) > 1e-9:  # import-time guard against editing mistakes
    raise RuntimeError("CHECK_WEIGHTS must sum to 1.0")


@dataclass(frozen=True)
class CheckResult:
    fraction: float  # 0..1
    detail: str


@dataclass(frozen=True)
class EvaluationInput:
    analysis: dict[str, Any]
    patch: str | None
    stack_trace: str
    trigger: Frame | None
    affected_file: str | None
    code_context: str | None
    historical_matches: list[dict[str, Any]]
    max_changed_lines: int
    error_message: str = ""
    caller_context: str | None = None


@dataclass(frozen=True)
class Evaluation:
    score: float
    passed: bool
    evidence_sufficient: bool
    checks: dict[str, CheckResult]
    feedback: str | None

    def grounded_without_patch(self, patch: str | None) -> bool:
        """Schema, trigger and evidence checks all pass, and no patch was proposed."""
        analysis_checks = ("schema", "trigger_grounding", "evidence_grounding")
        return not patch and all(self.checks[name].fraction == 1.0 for name in analysis_checks)

    def as_dict(self) -> dict[str, Any]:
        return {
            "quality_score": self.score,
            "quality_gate_passed": self.passed,
            "model_declared_evidence_sufficient": self.evidence_sufficient,
            "checks": {
                name: {
                    "score": round(CHECK_WEIGHTS[name] * c.fraction, 4),
                    "max": CHECK_WEIGHTS[name],
                    "blocking": name in BLOCKING_CHECKS,
                    "detail": c.detail,
                }
                for name, c in self.checks.items()
            },
        }


def _section(data: dict[str, Any], key: str) -> dict[str, Any]:
    value = data.get(key)
    return value if isinstance(value, dict) else {}


def _list(data: dict[str, Any], key: str) -> list[Any]:
    value = data.get(key)
    return value if isinstance(value, list) else []


def _squash(text: str) -> str:
    return " ".join(text.split())


# ------------------------------------------------------------------ individual checks


def check_schema(analysis: dict[str, Any]) -> CheckResult:
    if "output_error" in analysis:  # set by analyze_root_cause when the reply was not a JSON object
        return CheckResult(0.0, str(analysis["output_error"]))
    try:
        RCAOutput.model_validate(analysis)
    except ValidationError as exc:
        errors = exc.errors()
        listed = "; ".join(f"{'.'.join(map(str, e['loc'])) or 'root'}: {e['msg']}" for e in errors[:8])
        return CheckResult(max(0.0, 1.0 - len(errors) / 10), f"{len(errors)} schema error(s): {listed}")
    return CheckResult(1.0, "ok")


def check_trigger(analysis: dict[str, Any], trigger: Frame | None) -> CheckResult:
    if trigger is None:
        return CheckResult(0.0, "no application frame found in the stack trace")
    claimed = str(_section(analysis, "incident_summary").get("trigger_frame", "")).lower()
    base = trigger.path.replace("\\", "/").rsplit("/", 1)[-1].lower()
    has_file = base in claimed
    has_line = re.search(rf"(?<!\d){trigger.line}(?!\d)", claimed) is not None
    problems = []
    if not has_file:
        problems.append(f"trigger_frame must name file {base!r}")
    if not has_line:
        problems.append(f"trigger_frame must include failing line {trigger.line}")
    return CheckResult(0.5 * has_file + 0.5 * has_line, "; ".join(problems) or "ok")


def _source_text(source: str, inp: EvaluationInput) -> str | None:
    if source == "stack_trace":
        return inp.stack_trace
    if source == "code_context":
        # Quotes may come from the trigger window or from a caller window; both were shown to the model.
        texts = []
        for block in (inp.code_context, inp.caller_context):
            try:
                texts.append("\n".join(parse_code_window(block or "")[1]))
            except PatchError:
                continue
        return "\n".join(texts) or None
    if source == "historical_incidents":
        return format_history(inp.historical_matches) if inp.historical_matches else None
    return None


def verify_evidence(inp: EvaluationInput) -> list[str | None]:
    """One entry per 'observed' evidence item: None when its quote is verified, else the problem."""
    observed = [e for e in _list(inp.analysis, "evidence") if isinstance(e, dict) and e.get("kind") == "observed"]
    results: list[str | None] = []
    for idx, item in enumerate(observed, start=1):
        quote = _squash(str(item.get("quote") or ""))
        source = str(item.get("source") or "")
        text = _source_text(source, inp)
        if text is None:
            results.append(f"observed evidence #{idx} cites {source!r}, which was not retrieved/available")
        elif len(quote) < MIN_QUOTE_CHARS:
            results.append(f"observed evidence #{idx} needs a verbatim quote (>= {MIN_QUOTE_CHARS} chars)")
        elif quote not in _squash(text):
            results.append(f"observed evidence #{idx} quote not found verbatim in {source}: {quote[:60]!r}")
        else:
            results.append(None)
    return results


def check_evidence(inp: EvaluationInput) -> CheckResult:
    """Every 'observed' evidence quote must literally occur (whitespace-normalised) in its claimed source."""
    results = verify_evidence(inp)
    if not results:
        return CheckResult(0.0, "no 'observed' evidence with a verbatim quote was provided")
    problems = [r for r in results if r is not None]
    return CheckResult((len(results) - len(problems)) / len(results), "; ".join(problems) or "ok")


def verify_affected_files(inp: EvaluationInput) -> dict[str, bool]:
    """affected_files entry -> grounded (it is the retrieved file or appears in the stack trace)."""
    files = [f for f in _list(inp.analysis, "affected_files") if isinstance(f, str) and f.strip()]
    trace = inp.stack_trace.replace("\\", "/")
    return {f: path_matches(f, inp.affected_file) or f.replace("\\", "/").strip("/") in trace for f in files}


def check_affected_files(inp: EvaluationInput) -> CheckResult:
    grounded = verify_affected_files(inp)
    if not grounded:
        return CheckResult(0.5, "affected_files is empty")
    ungrounded = [f for f, ok in grounded.items() if not ok]
    detail = (
        "ok"
        if not ungrounded
        else "files not present in the stack trace or retrieved context: " + ", ".join(repr(f) for f in ungrounded)
    )
    return CheckResult((len(grounded) - len(ungrounded)) / len(grounded), detail)


_KEY_ERROR_RE = re.compile(r"""KeyError: ['"](?P<name>[^'"\n]+)['"]""")
_ATTRIBUTE_RE = re.compile(r"""has no attribute ['"](?P<name>\w+)['"]""")


def _line_names(line: str) -> tuple[set[str], set[str], bool] | None:
    """(string-literal subscript keys, attribute names, has a dynamic subscript/getattr) of one source line.

    None when the line cannot be parsed on its own (then the check does not apply)."""
    code = line.strip()
    for candidate in (code, code + " pass", code.rstrip(":") if code.endswith(":") else None):
        if not candidate:
            continue
        try:
            tree = ast.parse(candidate)
        except SyntaxError:
            continue
        keys, attrs, dynamic = set(), set(), False
        for node in ast.walk(tree):
            if isinstance(node, ast.Subscript):
                if isinstance(node.slice, ast.Constant) and isinstance(node.slice.value, str):
                    keys.add(node.slice.value)
                else:
                    dynamic = True
            elif isinstance(node, ast.Attribute):
                attrs.add(node.attr)
            elif isinstance(node, ast.Call) and getattr(node.func, "id", None) in {"getattr", "get"}:
                dynamic = True
        return keys, attrs, dynamic
    return None


def check_trace_code_consistency(inp: EvaluationInput) -> CheckResult:
    """Does the retrieved failing line contain the key / attribute the error names?

    When the error says KeyError 'user_id' but the retrieved line only reads payload["account_id"], the running
    code differs from what was retrieved (deploy drift, wrong branch): the analysis cannot be grounded and must
    say so. Not applicable (score 1.0) when the error names nothing, the line is unavailable or unparsable, or
    the line uses dynamic keys - so the check never fails on what it cannot judge.
    """
    first_line = (inp.error_message or "").strip().splitlines()[:1]
    message = first_line[0] if first_line else ""
    key = _KEY_ERROR_RE.search(message)
    attr = _ATTRIBUTE_RE.search(message)
    if not (key or attr) or not inp.code_context or inp.trigger is None:
        return CheckResult(1.0, "not applicable")
    try:
        start, lines = parse_code_window(inp.code_context)
    except PatchError:
        return CheckResult(1.0, "not applicable")
    index = inp.trigger.line - start
    if not 0 <= index < len(lines):
        return CheckResult(1.0, "not applicable: failing line outside the retrieved window")
    names = _line_names(lines[index])
    if names is None:
        return CheckResult(1.0, "not applicable: failing line could not be parsed")
    keys, attrs, dynamic = names
    if key:
        wanted, present, kind = key.group("name"), keys, "key"
    else:
        wanted, present, kind = attr.group("name") if attr else "", attrs, "attribute"
    if dynamic or not present or wanted in present:
        return CheckResult(1.0, "ok" if wanted in present else "not applicable: dynamic access on the failing line")
    return CheckResult(
        0.0,
        f"the error names {kind} {wanted!r} but the retrieved failing line (line {inp.trigger.line}) only uses "
        f"{sorted(present)}: the running code probably differs from the retrieved source. Do not propose a patch; "
        "set evidence_sufficient to false and explain the discrepancy in uncertainties",
    )


def _window_text(block: str | None) -> str:
    try:
        return "\n".join(parse_code_window(block or "")[1])
    except PatchError:
        return ""


def check_fix_location(inp: EvaluationInput) -> CheckResult:
    """Warn when a patch to the failing function sits next to a diagnosis that rests on caller code.

    When an 'observed' quote occurs only in the caller windows (not in the failing file's window), the model is
    reasoning about what a caller passed, and a guard in the callee can merely hide the symptom. This is a
    *warning*, not a veto (quality-gate-v8). It was blocking in v6/v7 and was measured live: in 15 runs it fired 13
    times and left 14 runs without a patch, including 3 of 3 runs of the control case where the failing function
    really was at fault and the model quoted the caller only to clear it. Nothing deterministic separated those
    from the real blame cases, and every proposal ends with a human anyway, so the reviewer is told (guidance card,
    gate breakdown, patch label) instead of being denied the patch.
    """
    if not inp.patch or not inp.caller_context:
        return CheckResult(1.0, "ok")
    own, callers = _squash(_window_text(inp.code_context)), _squash(_window_text(inp.caller_context))
    cited = []
    for item in _list(inp.analysis, "evidence"):
        if not (isinstance(item, dict) and item.get("kind") == "observed" and item.get("source") == "code_context"):
            continue
        quote = _squash(str(item.get("quote") or ""))
        if len(quote) >= MIN_QUOTE_CHARS and quote in callers and quote not in own:
            cited.append(quote[:50])
    if not cited:
        return CheckResult(1.0, "ok")
    return CheckResult(
        0.0,
        "the evidence cites code that only occurs in a caller (" + "; ".join(repr(q) for q in cited[:2]) + ") but the "
        "patch changes the failing function, which may only hide the symptom. If the cause is in the caller, "
        "say so in uncertainties and leave unified_diff empty",
    )


def check_self_assessment(analysis: dict[str, Any]) -> CheckResult:
    flow = _section(analysis, "control_flow")
    if flow.get("needs_human_review") is True:
        return CheckResult(0.0, "model asked for human review")
    conf = flow.get("self_assessed_confidence")
    if isinstance(conf, (int, float)) and not isinstance(conf, bool):
        return CheckResult(max(0.0, min(1.0, float(conf))), f"model self-assessed {float(conf):.2f} (uncalibrated)")
    return CheckResult(0.0, "self_assessed_confidence missing")


MIN_COPY_CHARS = 15


def surviving_copies(hunks: Any, patched: list[str]) -> list[tuple[str, int]]:
    """Statements the patch removed that still appear, character for character, in the patched source.

    A copied block or a sibling branch usually carries the same defect; fixing one copy leaves the failing input able to
    reach the other (measured: a patch for an empty-table crash fixed one of two identical `num_cols = ...` lines).
    Only substantial code lines count (not comments, brackets or one-word lines), so this stays a warning with few false
    alarms; it never blocks.
    """
    added = Counter(t.strip() for hunk in hunks for op, t in hunk.ops if op == "+")
    kept = Counter(line.strip() for line in patched) - added  # a line the patch itself writes back is not a survivor
    found: list[tuple[str, int]] = []
    for hunk in hunks:
        for op, raw in hunk.ops:
            text = raw.strip()
            if op != "-" or len(text) < MIN_COPY_CHARS or text.startswith("#") or not any(c.isalnum() for c in text):
                continue
            if kept.get(text, 0) > 0 and all(text != f for f, _ in found):
                found.append((text, kept[text]))
    return found


def check_patch(inp: EvaluationInput) -> dict[str, CheckResult]:
    names = ("diff_wellformed", "diff_applies", "patch_minimal", "patch_locality", "patch_effective")
    if not inp.patch:
        return {name: CheckResult(0.0, "no patch provided") for name in names}
    try:
        parsed = parse_unified_diff(inp.patch)
    except PatchError as exc:
        return {name: CheckResult(0.0, f"diff is malformed: {exc}") for name in names}

    results: dict[str, CheckResult] = {}
    problems = []
    if not path_matches(parsed.path, inp.affected_file):
        problems.append(f"diff header path {parsed.path!r} must be {inp.affected_file!r}")
    if parsed.file_count != 1:
        problems.append("diff must touch exactly one file")
    results["diff_wellformed"] = CheckResult(1.0 - 0.5 * len(problems), "; ".join(problems) or "ok")

    changed, limit = parsed.changed_lines, inp.max_changed_lines
    results["patch_minimal"] = CheckResult(
        1.0 if changed <= limit else 0.5 if changed <= 2 * limit else 0.0, f"{changed} changed lines (limit {limit})"
    )

    if not inp.code_context:
        reason = "source was not retrieved - the patch cannot be verified against the code"
        for name in ("diff_applies", "patch_locality", "patch_effective"):
            results[name] = CheckResult(0.0, reason)
        return results
    try:
        start, lines = parse_code_window(inp.code_context)
        positions: list[int] = []
        patched = apply_hunks(lines, parsed.hunks, start - 1, positions)
    except PatchError as exc:
        results["diff_applies"] = CheckResult(
            0.0, f"patch does not apply to the retrieved source: {exc}. Copy context/removed lines verbatim"
        )
        results["patch_locality"] = CheckResult(0.0, "patch not applicable")
        results["patch_effective"] = CheckResult(0.0, "patch not applicable")
        return results

    results["diff_applies"] = CheckResult(1.0, "ok")
    effective = [x.strip() for x in patched] != [x.strip() for x in lines]
    results["patch_effective"] = (
        CheckResult(1.0, "ok") if effective else CheckResult(0.0, "patch changes only whitespace / nothing")
    )
    surviving = surviving_copies(parsed.hunks, patched)
    if surviving:
        results["patch_locality"] = CheckResult(
            0.4,
            f"the patch removes {surviving[0][0]!r}, which still occurs {surviving[0][1]} more time(s) in the "
            "retrieved source: the same defect may exist there. Fix every copy the failing input can reach, "
            "or say why not",
        )
    elif inp.trigger is None:
        results["patch_locality"] = CheckResult(0.4, "no failing line to compare against")
    else:
        near = any(abs(pos - inp.trigger.line) <= LOCALITY_RADIUS for pos in positions)
        results["patch_locality"] = (
            CheckResult(1.0, "ok")
            if near
            else CheckResult(
                0.4,
                f"patch is more than {LOCALITY_RADIUS} lines from failing line {inp.trigger.line}; justify or move it",
            )
        )
    return results


# ------------------------------------------------------------------ aggregate


def evaluate(inp: EvaluationInput, threshold: float) -> Evaluation:
    checks: dict[str, CheckResult] = {
        "schema": check_schema(inp.analysis),
        "trigger_grounding": check_trigger(inp.analysis, inp.trigger),
        "evidence_grounding": check_evidence(inp),
        "affected_files_grounding": check_affected_files(inp),
        "trace_code_consistency": check_trace_code_consistency(inp),
        "fix_location": check_fix_location(inp),
        "self_assessment": check_self_assessment(inp.analysis),
        **check_patch(inp),
    }
    score = round(sum(CHECK_WEIGHTS[name] * c.fraction for name, c in checks.items()), 4)
    blocking_failures = [name for name in BLOCKING_CHECKS if checks[name].fraction < 1.0]
    sufficient = _section(inp.analysis, "control_flow").get("evidence_sufficient") is not False
    passed = score >= threshold and not blocking_failures and sufficient

    feedback = None
    if not passed:
        lines = [
            f"- {name}{' [BLOCKING]' if name in BLOCKING_CHECKS else ''} "
            f"({CHECK_WEIGHTS[name] * c.fraction:.2f}/{CHECK_WEIGHTS[name]:.2f}): {c.detail}"
            for name, c in checks.items()
            if c.fraction < 1.0
        ]
        feedback = (
            f"Quality score {score:.2f} (required {threshold:.2f}; all BLOCKING checks must pass). "
            "Fix these problems:\n" + "\n".join(lines)
        )
    return Evaluation(score=score, passed=passed, evidence_sufficient=sufficient, checks=checks, feedback=feedback)
