"""The category hint: a reviewer's pointer when the code shows the failing value comes from an HTTP response."""

import pytest

from src.agent.patching import format_code_window
from src.services.category_hints import external_response_hint
from src.services.evidence_graph import build_evidence_graph
from src.services.guidance import build_guidance

SEARCH = (
    "import requests\n\n\ndef search_repos(query):\n"
    '    payload = requests.get("https://api.example.test/search", params={"q": query}, timeout=5).json()\n'
    '    return [item["name"] for item in payload["items"]]\n'
)


def _analysis(code=SEARCH, category="missing_key", quote='return [item["name"] for item in payload["items"]]', **extra):
    window = format_code_window(code.splitlines(), 1)
    return {
        "root_cause_category": category,
        "evidence": [{"kind": "observed", "source": "code_context", "quote": quote}],
        "source_windows": {"code_context": window},
        **extra,
    }


def test_the_live_miss_is_flagged():
    hint = external_response_hint(_analysis())
    assert hint and "`payload`" in hint and "requests.get" in hint and "invalid_external_response" in hint


@pytest.mark.parametrize("category", ["missing_key", "null_reference", "attribute_error", "type_error"])
def test_every_internal_category_gets_the_hint(category):
    assert external_response_hint(_analysis(category=category))


@pytest.mark.parametrize(
    "category", ["invalid_external_response", "dependency_unavailable", "logic_error", "unknown", None]
)
def test_other_categories_never_get_it(category):
    assert external_response_hint(_analysis(category=category)) is None


def test_one_hop_through_a_response_variable():
    code = (
        "import httpx\n\n\ndef load(url):\n"
        "    response = httpx.get(url, timeout=5)\n"
        "    body = response.json()\n"
        '    return body["rows"][0]\n'
    )
    hint = external_response_hint(_analysis(code=code, quote='return body["rows"][0]'))
    assert hint and "`body`" in hint and "httpx.get" in hint


@pytest.mark.parametrize(
    "code, quote",
    [
        # a lookup in our own dictionary
        (
            'import os\n\nSETTINGS = {"a": 1}\n\n\ndef f(key):\n    cfg = dict(SETTINGS)\n    return cfg[key]\n',
            "return cfg[key]",
        ),
        # a value computed locally, not fetched
        ('def f(rows):\n    index = {r["id"]: r for r in rows}\n    return index["x"]\n', 'return index["x"]'),
        # the name is never assigned in the window (a parameter): nothing to conclude
        ('def f(payload):\n    return payload["items"]\n', 'return payload["items"]'),
        # a call named like an HTTP verb on something that is not a client
        ('def f(cache):\n    value = cache.get("k")\n    return value["x"]\n', 'return value["x"]'),
    ],
)
def test_no_hint_when_the_value_is_not_from_http(code, quote):
    assert external_response_hint(_analysis(code=code, quote=quote)) is None


def test_no_hint_without_a_window_an_observed_code_quote_or_with_garbage():
    assert external_response_hint(_analysis(source_windows={})) is None
    assert (
        external_response_hint(
            {**_analysis(), "evidence": [{"kind": "inference", "source": "code_context", "quote": "payload[x]"}]}
        )
        is None
    )
    assert (
        external_response_hint(
            {**_analysis(), "evidence": [{"kind": "observed", "source": "stack_trace", "quote": 'payload["items"]'}]}
        )
        is None
    )
    for junk in (None, [], "x", {}, {"root_cause_category": "missing_key", "evidence": "nope", "source_windows": 3}):
        assert external_response_hint(junk) is None


def test_names_that_are_not_data_are_skipped():
    code = 'import requests\n\n\ndef f():\n    self = requests.get("u")\n    return self.value\n'
    assert external_response_hint(_analysis(code=code, quote="return self.value")) is None


def test_the_hint_reaches_the_reviewer_in_the_guidance_card_and_the_evidence_flags():
    incident = {
        "incident_id": "i1", "status": "awaiting_approval", "error_category": None, "analysis": _analysis(),
        "suggested_patch": None, "quality_score": 0.99,
    }  # fmt: skip
    steps = [step["text"] for step in build_guidance(incident)["next_steps"]]
    assert any("HTTP call" in text for text in steps)
    flags = build_evidence_graph(incident)["flags"]
    assert any(flag.startswith("category:") and "payload" in flag for flag in flags)


def test_a_correct_category_changes_nothing_in_the_guidance():
    incident = {
        "incident_id": "i1",
        "status": "awaiting_approval",
        "error_category": None,
        "analysis": _analysis(category="invalid_external_response"),
    }
    assert not any("HTTP call" in step["text"] for step in build_guidance(incident)["next_steps"])


def test_a_session_object_counts_only_when_the_window_builds_it_from_an_http_client():
    web = 'def profile_header(session):\n    user = session.get("user")\n    return user["first"]\n'
    assert external_response_hint(_analysis(code=web, quote='return user["first"]')) is None  # a web session, not HTTP
    client = (
        "import requests\n\n\ndef load(url):\n    session = requests.Session()\n"
        '    data = session.get(url, timeout=5).json()\n    return data["rows"]\n'
    )
    hint = external_response_hint(_analysis(code=client, quote='return data["rows"]'))
    assert hint and "`data`" in hint


def test_the_dataset_check_has_no_false_alarms_and_still_catches_the_known_miss(capsys):
    from evals import category_hint_check

    assert category_hint_check.main([]) == 0  # exit code 1 means a case labelled something else got the hint
    out = capsys.readouterr().out
    assert "holdout/ho-upstream-shape" in out and "false alarms (label is something else):        0" in out


@pytest.mark.parametrize("category", [[], {}, [1], {"a": 1}, 3.5, True, b"missing_key", ("missing_key",)])
def test_a_category_of_any_type_is_never_an_error(category):
    """Hypothesis found this in CI: `root_cause_category: []` raised 'unhashable type' from a set lookup."""
    assert external_response_hint(_analysis(category=category)) is None


@pytest.mark.parametrize(
    "evidence", ["x", 5, 2.5, None, {}, {"kind": "observed"}, [None, 1, "a", [], {"kind": "observed"}]]
)
def test_evidence_of_any_shape_is_never_an_error(evidence):
    analysis = {**_analysis(), "evidence": evidence}
    assert external_response_hint(analysis) is None


@pytest.mark.parametrize(
    "quote", [None, 5, ["a"], {"b": 1}, "", "\u200b", pytest.param("payload" * 5000, id="very-long")]
)
def test_a_quote_of_any_type_is_never_an_error(quote):
    analysis = {**_analysis(), "evidence": [{"kind": "observed", "source": "code_context", "quote": quote}]}
    external_response_hint(analysis)  # must not raise; the answer depends on the quote
