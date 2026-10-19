"""Structure-aware fuzzing of everything the model's reply flows through.

A model can return valid JSON with a wrong type in any field, a truncated string, an enormous value, odd Unicode or
plain prose. Whatever it returns, the schema check, the quality gate and the whole workflow must stay *total*: no
exception escapes, scores stay in range, the loop terminates within its budget, and the gate never passes something
its own blocking checks reject. Fuzzing starts from a valid analysis and corrupts it at known positions, which reaches
far deeper than random text would.
"""

import copy
import re
from typing import Any

from hypothesis import given, settings
from hypothesis import strategies as st
from pydantic import ValidationError

from src.agent.evaluation import BLOCKING_CHECKS, EvaluationInput, evaluate
from src.agent.graph import build_graph
from src.agent.parsing import (
    clean_text,
    compute_fingerprint,
    get_application_frames,
    get_trigger_frame,
    split_embedded_trace,
)
from src.agent.schemas import RCAOutput
from src.agent.state import initial_state
from src.config import settings as app_settings
from src.logging_config import find_secrets, redact
from tests.unit.factories import BAD_DIFF, ERROR, GOOD_DIFF, SOURCE_CONTEXT, TRACE, FakeLLM, make_analysis
from tests.unit.test_properties import JSON_VALUE, PROFILE

DELETE = object()

# Positions of a valid analysis that get corrupted: top-level fields, nested objects and list elements.
PATHS = [
    ("incident_summary",), ("incident_summary", "title"), ("incident_summary", "severity"),
    ("root_cause_category",), ("hypotheses",), ("hypotheses", 0), ("hypotheses", 0, "likelihood"),
    ("evidence",), ("evidence", 0), ("evidence", 0, "quote"), ("evidence", 0, "source"), ("evidence", 1, "kind"),
    ("evidence", 1, "quote"), ("affected_files",), ("diagnostic_chain",), ("diagnostic_chain", "primary_root_cause"),
    ("diagnostic_chain", "primary_root_cause", "hypothesis_id"), ("patch_remediation",),
    ("patch_remediation", "unified_diff"), ("uncertainties",), ("tests_to_run",), ("control_flow",),
    ("control_flow", "self_assessed_confidence"), ("control_flow", "evidence_sufficient"),
]  # fmt: skip

WEIRD_TEXT = st.text(
    alphabet=st.characters(blacklist_categories=("Cs",)),  # every Unicode category except lone surrogates
    max_size=40,
) | st.sampled_from(["​", "‮", "é", "\x00", "\x1b[31m", "a" * 20_000, "﻿", "\r\n"])

VALUE = JSON_VALUE | WEIRD_TEXT | st.just(DELETE)


def _mutate(edits: list[tuple[tuple[Any, ...], Any]]) -> dict[str, Any]:
    analysis = make_analysis()
    for path, value in edits:
        target: Any = analysis
        try:
            for step in path[:-1]:
                target = target[step]
            if value is DELETE:
                del target[path[-1]]
            else:
                target[path[-1]] = copy.deepcopy(value)
        except (KeyError, IndexError, TypeError):
            continue  # an earlier edit already replaced this branch: skipping it is part of the fuzz
    return analysis


# Type-correct corruptions: the schema usually still validates, so the semantic checks behind it (quote verification,
# trigger matching, diff application, locality) are reached far more often than with wrong-typed values.
BENIGN_PATHS = [
    ("incident_summary", "trigger_frame"), ("evidence", 0, "quote"), ("evidence", 1, "quote"),
    ("evidence", 0, "claim"), ("affected_files",), ("patch_remediation", "unified_diff"),
    ("patch_remediation", "explanation"), ("control_flow", "self_assessed_confidence"),
    ("control_flow", "evidence_sufficient"), ("root_cause_category",),
]  # fmt: skip
BENIGN_VALUE = (
    WEIRD_TEXT
    | st.floats(allow_nan=False, allow_infinity=False, min_value=-2, max_value=2)
    | st.booleans()
    | st.lists(WEIRD_TEXT, max_size=3)
    | st.sampled_from(["src/app.py", "src/app.py:6 -> load_user()", GOOD_DIFF, BAD_DIFF, 'name = profile["name"]'])
)
_WRONG_TYPED = st.lists(st.tuples(st.sampled_from(PATHS), VALUE), max_size=4)
_TYPE_PRESERVING = st.lists(st.tuples(st.sampled_from(BENIGN_PATHS), BENIGN_VALUE), max_size=3)
CORRUPTED = (_WRONG_TYPED | _TYPE_PRESERVING).map(_mutate)
PATCHES = st.sampled_from([GOOD_DIFF, BAD_DIFF, None, ""]) | st.text(max_size=300)


@PROFILE
@given(CORRUPTED)
def test_schema_validation_only_ever_raises_validation_error(analysis):
    try:
        parsed = RCAOutput.model_validate(analysis)
    except ValidationError:
        return
    assert RCAOutput.model_validate(parsed.model_dump()) == parsed  # a normalised analysis is a fixed point


@PROFILE
@given(CORRUPTED, PATCHES)
def test_the_quality_gate_is_total_and_never_passes_what_its_blocking_checks_reject(analysis, patch):
    inp = EvaluationInput(
        analysis=analysis,
        patch=patch,
        stack_trace=TRACE,
        trigger=get_trigger_frame(TRACE),
        affected_file="src/app.py",
        code_context=SOURCE_CONTEXT.window_text,
        historical_matches=[],
        max_changed_lines=40,
        error_message=ERROR,
    )
    result = evaluate(inp, 0.85)
    assert 0.0 <= result.score <= 1.0
    assert all(0.0 <= check.fraction <= 1.0 for check in result.checks.values())
    if result.passed:
        assert result.score >= 0.85
        assert all(result.checks[name].fraction == 1.0 for name in BLOCKING_CHECKS)
        assert result.feedback is None
    else:
        assert result.feedback, "a rejected analysis must come with feedback for the next attempt"


REPLY = CORRUPTED | st.text(max_size=300) | CORRUPTED.map(lambda a: __import__("json").dumps(a)[:-7])  # last: truncated


@settings(PROFILE, max_examples=60)
@given(st.lists(REPLY, min_size=1, max_size=3))
def test_the_whole_workflow_terminates_in_a_known_state_whatever_the_model_replies(replies):
    budget = app_settings.max_analysis_iterations
    llm = FakeLLM((replies + [replies[-1]] * budget)[:budget])
    graph = build_graph(lambda *a: SOURCE_CONTEXT, lambda *a: [], lambda temperature: llm)
    final = graph.invoke(
        initial_state("00000000-0000-0000-0000-000000000001", ERROR, TRACE, "o/r"), config={"recursion_limit": 25}
    )
    assert final["workflow_status"] in {"accepted", "needs_review", "failed"}
    assert 1 <= final["iterations"] <= budget and llm.calls <= budget
    if final["workflow_status"] == "accepted":  # acceptance must always be backed by a passing gate
        assert final["quality_gate_passed"] and final["root_cause_analysis"]["evaluation"]["quality_gate_passed"]


# ------------------------------------------------------------------ text that reaches the parsers


@PROFILE
@given(st.text(alphabet=st.characters(blacklist_categories=("Cs",)), max_size=600))
def test_trace_parsers_are_total_on_arbitrary_text(text):
    cleaned = clean_text(text, 200)
    assert "\x00" not in cleaned and len(cleaned) <= 400
    message, trace = split_embedded_trace(text, text)
    assert isinstance(message, str) and isinstance(trace, str)
    assert all(frame.line >= 0 for frame in get_application_frames(text))
    fingerprint = compute_fingerprint("o/r", text, text)
    assert re.fullmatch(r"[0-9a-f]{64}", fingerprint) and fingerprint == compute_fingerprint("o/r", text, text)


TOKEN = "ghp_" + "Ab1" * 12


@PROFILE
@given(st.text(max_size=80), st.text(max_size=80))
def test_redaction_is_idempotent_and_removes_a_planted_credential(before, after):
    text = f"{before} {TOKEN} {after}"
    once = redact(text)
    assert TOKEN not in once and redact(once) == once
    assert "github_token" in find_secrets(text)
