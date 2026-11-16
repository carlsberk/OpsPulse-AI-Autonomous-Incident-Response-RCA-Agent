"""Property-based tests: invariants that must hold for *any* input, not just the examples we thought of.

Every parser here receives untrusted text (log lines, webhook payloads, model output), so the properties are mostly
"never raises anything but the documented error" and "the safety invariant holds".
"""

import contextlib
import difflib
import json
import os
import re

from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from src.agent.nodes import extract_json_object
from src.agent.patching import PatchError, apply_hunks, parse_unified_diff
from src.agent.prompts import _PROMPT_TAGS, neutralize_tags
from src.integrations.alertmanager import AlertmanagerPayloadError, to_incidents
from src.integrations.sentry import SentryPayloadError, to_incident
from src.services.ask_service import MAX_ANSWER_CHARS, parse_answer
from src.services.guidance import build_guidance

# Reproducible by default (derandomised: a red build is always a real, repeatable bug). The weekly Security workflow
# sets HYPOTHESIS_EXPLORE=1 and many more examples to hunt for new counterexamples with random seeds.
_EXPLORE = os.environ.get("HYPOTHESIS_EXPLORE") == "1"
PROFILE = settings(
    max_examples=int(os.environ.get("HYPOTHESIS_EXAMPLES", "150")),
    derandomize=not _EXPLORE,
    deadline=None,
    suppress_health_check=[HealthCheck.too_slow],
)

# JSON-like values for webhook payloads
JSON_LEAF = st.none() | st.booleans() | st.integers(-5, 10**6) | st.floats(allow_nan=False) | st.text(max_size=30)
JSON_VALUE = st.recursive(
    JSON_LEAF,
    lambda inner: st.lists(inner, max_size=4) | st.dictionaries(st.text(max_size=12), inner, max_size=5),
    max_leaves=25,
)

# source lines: no leading '-', '+', '@' so a line can never be mistaken for diff syntax
LINE = st.text(alphabet="abcdefghij 0123456789_=()[]:,.", min_size=0, max_size=24)


@PROFILE
@given(
    original=st.lists(LINE, min_size=1, max_size=30),
    start=st.integers(0, 29),
    removed=st.integers(0, 4),
    inserted=st.lists(LINE, max_size=4),
)
def test_a_diff_between_two_texts_applies_and_reproduces_the_second_text(original, start, removed, inserted):
    start = min(start, len(original))
    modified = original[:start] + inserted + original[start + removed :]
    if modified == original:
        return
    diff = "".join(
        difflib.unified_diff(
            [line + "\n" for line in original], [line + "\n" for line in modified], "a/f.py", "b/f.py", n=3
        )
    )
    parsed = parse_unified_diff(diff)
    assert apply_hunks(list(original), parsed.hunks) == modified


@PROFILE
@given(st.text(max_size=400))
def test_diff_parser_only_ever_raises_patch_error(text):
    with contextlib.suppress(PatchError):
        parse_unified_diff(text)


@PROFILE
@given(st.text(max_size=400))
def test_json_extraction_returns_a_dict_or_none(text):
    value = extract_json_object(text)
    assert value is None or isinstance(value, dict)


_TAG_TOKENS = [f"<{t}>" for t in _PROMPT_TAGS] + [f"</{t}>" for t in _PROMPT_TAGS] + ["<", ">", "/", " ", "x", "<<"]
_ANY_TAG = re.compile(r"<\s*/?\s*(?:" + "|".join(_PROMPT_TAGS) + r")\s*>", re.IGNORECASE)


@PROFILE
@given(st.lists(st.sampled_from(_TAG_TOKENS) | st.text(max_size=6), max_size=12).map("".join))
def test_untrusted_text_can_never_contain_a_prompt_delimiter_after_neutralisation(text):
    cleaned = neutralize_tags(text)
    assert not _ANY_TAG.search(cleaned)
    assert neutralize_tags(cleaned) == cleaned  # idempotent


@PROFILE
@given(JSON_VALUE)
def test_sentry_translation_returns_an_incident_or_the_documented_error(payload):
    try:
        incident = to_incident(payload if isinstance(payload, dict) else {"data": payload})
    except SentryPayloadError:
        return
    assert len(incident.error_message) <= 20_000 and len(incident.stack_trace) <= 100_000


@PROFILE
@given(JSON_VALUE)
def test_alertmanager_translation_returns_incidents_or_the_documented_error(payload):
    body = payload if isinstance(payload, dict) else {"alerts": payload}
    body = {"version": "4", **body} if isinstance(body.get("alerts"), list) else body
    try:
        incidents, dropped = to_incidents(body, "repository")
    except AlertmanagerPayloadError:
        return
    assert dropped >= 0
    for alert in incidents:
        assert len(alert.error_message) <= 20_000 and len(alert.stack_trace) <= 100_000
        assert alert.repo_name is None or "/" in alert.repo_name


SECTIONS = {"summary": "status: failed", "evidence": "quote text here", "patch": "--- a/x"}


@PROFILE
@given(st.text(max_size=300) | JSON_VALUE.map(json.dumps))
def test_model_answers_are_always_bounded_and_cite_only_known_sections(text):
    out = parse_answer(text, SECTIONS)
    assert len(out["answer"]) <= MAX_ANSWER_CHARS
    assert set(out["cited_sections"]) <= set(SECTIONS)
    assert isinstance(out["grounded"], bool) and isinstance(out["follow_ups"], list)
    assert len(out["follow_ups"]) <= 3 and all(len(f) <= 100 for f in out["follow_ups"])


_CHECK = st.fixed_dictionaries(
    {
        "score": st.floats(0, 1, allow_nan=False) | st.none(),
        "max": st.floats(0, 1, allow_nan=False) | st.none(),
        "blocking": st.booleans(),
        "detail": st.text(max_size=40) | st.none(),
    }
)


@PROFILE
@given(
    status=st.sampled_from(
        ["queued", "processing", "failed", "needs_review", "analysis_ready", "awaiting_approval", "x"]
    ),
    category=st.none() | st.sampled_from(["llm_rate_limited", "no_code_fix", "unknown_category"]),
    checks=st.dictionaries(st.sampled_from(["diff_applies", "schema", "fix_location", "novel_check"]), _CHECK),
    patch=st.none() | st.text(max_size=200),
)
def test_guidance_is_total_for_any_stored_record(status, category, checks, patch):
    incident = {
        "status": status,
        "error_category": category,
        "quality_score": 0.5,
        "iterations": 1,
        "analysis": {"evaluation": {"checks": checks}},
        "suggested_patch": patch,
    }
    guide = build_guidance(incident)
    assert guide["state"] in {"ok", "attention", "blocked", "waiting"} and guide["headline"]
    assert all(c["advice"] for c in guide["failed_checks"])


_FRAME = st.fixed_dictionaries(
    {k: JSON_VALUE for k in ("abs_path", "filename", "lineno", "function", "context_line", "in_app")}
)
_SENTRY_EVENT = st.fixed_dictionaries(
    {
        "event_id": st.text(min_size=1, max_size=12) | JSON_VALUE,
        "project": JSON_VALUE,
        "title": JSON_VALUE,
        "exception": JSON_VALUE
        | st.fixed_dictionaries(
            {
                "values": JSON_VALUE
                | st.lists(
                    st.fixed_dictionaries(
                        {
                            "type": JSON_VALUE,
                            "value": JSON_VALUE,
                            "stacktrace": JSON_VALUE | st.fixed_dictionaries({"frames": JSON_VALUE | st.lists(_FRAME)}),
                        }
                    ),
                    max_size=3,
                )
            }
        ),
    }
)


@PROFILE
@given(_SENTRY_EVENT)
def test_sentry_translation_survives_wrongly_typed_fields_at_every_known_position(event):
    with contextlib.suppress(SentryPayloadError):
        to_incident({"data": {"event": event}})


def test_sentry_stacktrace_given_as_a_list_is_a_payload_problem_not_a_server_error():
    payload = {"data": {"event": {"event_id": "1", "title": "t", "exception": {"values": [{"stacktrace": [1]}]}}}}
    incident = to_incident(payload)  # used to raise AttributeError -> HTTP 500 on a correctly signed request
    assert incident.stack_trace == "" and incident.error_message == "t"


def test_sentry_exception_values_of_the_wrong_type_are_ignored():
    for values in (True, 5, "text", {"a": 1}, None, [None, 3, "x"]):
        payload = {"data": {"event": {"event_id": "1", "title": "t", "exception": {"values": values}}}}
        assert to_incident(payload).error_message == "t"


# ---- input that reaches the server before anything has authenticated it, or that is stored as is -----------------

from src.agent.parsing import clean_text, compute_fingerprint, extract_exception_type, extract_frames  # noqa: E402
from src.integrations.sentry import signature_is_valid  # noqa: E402
from src.services.incident_service import _storable  # noqa: E402

_ANY_TEXT = st.text(max_size=300)
_NASTY = st.text(
    alphabet=st.sampled_from(["\x00", "\ud800", "\udfff", " ", "[", "{", '"', "\\", "é", "😀", "a"]), max_size=40
)


@PROFILE
@given(
    st.binary(max_size=400) | _NASTY.map(lambda s: s.encode("utf-8", "surrogatepass")), st.text(max_size=70) | st.none()
)
def test_the_sentry_signature_check_never_raises_for_any_body_or_header(body, provided):
    # It runs before the signature is known to be valid (an anonymous client controls both arguments). It once raised
    # UnicodeEncodeError on a lone surrogate escape and RecursionError on deep nesting: HTTP 500 instead of 401.
    assert signature_is_valid("secret", body, provided) in (True, False)


@PROFILE
@given(_NASTY | _ANY_TEXT)
def test_storable_text_always_encodes_as_utf8_and_has_no_nul(text):
    stored = _storable(text)
    stored.encode("utf-8")  # raises on a lone surrogate
    assert "\x00" not in stored


@PROFILE
@given(_NASTY | _ANY_TEXT)
def test_cleaned_text_has_no_nul_or_carriage_return(text):
    cleaned = clean_text(text)
    assert "\x00" not in cleaned and "\r" not in cleaned


@PROFILE
@given(_ANY_TEXT, _ANY_TEXT, _ANY_TEXT)
def test_fingerprint_and_trace_parsing_are_total_and_stable(repo, message, trace):
    first = compute_fingerprint(repo, message, trace)
    assert re.fullmatch(r"[0-9a-f]{64}", first) and compute_fingerprint(repo, message, trace) == first
    frames, language = extract_frames(trace)
    assert isinstance(language, str) and all(f.line >= 0 for f in frames)
    assert extract_exception_type(message, trace)  # falls back to "UnknownError", never empty or raising
