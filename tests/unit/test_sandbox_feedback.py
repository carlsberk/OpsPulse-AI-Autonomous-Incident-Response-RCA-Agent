"""Opt-in sandbox feedback: a patch the tests reject gets one more attempt, and only a better result replaces it."""

import pytest

from src.agent.sandbox_feedback import build_feedback, is_improvement, needs_repair
from src.integrations.github import PullRequestResult
from src.integrations.sandbox import SandboxReport, SandboxRun, failed_tests
from src.services import incident_service, remediation_service
from tests.unit.conftest import API_HEADERS
from tests.unit.factories import BAD_DIFF, incident_payload, make_analysis
from tests.unit.factories import GOOD_DIFF as RAW_GOOD_DIFF

GOOD_DIFF = RAW_GOOD_DIFF.strip()  # replies are stored stripped
SECOND_DIFF = GOOD_DIFF.replace('"missing profile"', '"profile is missing"')
FAILING = "FAILED tests/test_app.py::test_load_user - ValueError\nE   ValueError: missing profile"


def run(code, text=""):
    return SandboxRun(code, text, failed_tests(text))


def report(status, after_text=FAILING):
    return SandboxReport(status, f"summary {status}", "abc", run(1, FAILING), run(1, after_text))


# ------------------------------------------------------------------ pure helpers


@pytest.mark.parametrize(
    ("status", "expected"),
    [
        ("still_failing", True),
        ("regression", True),
        ("partly_fixed", False),
        ("fixes_failing_tests", False),
        ("no_regression", False),
        ("timeout", False),
        ("error", False),
        ("unavailable", False),
    ],
)
def test_only_verdicts_about_the_patch_trigger_a_repair(status, expected):
    assert needs_repair({"status": status}) is expected


def test_a_repaired_patch_must_be_strictly_better():
    order = ["regression", "still_failing", "partly_fixed", "no_regression", "fixes_failing_tests"]
    for i, old in enumerate(order):
        for j, new in enumerate(order):
            assert is_improvement({"status": old}, {"status": new}) is (j > i)
    assert not is_improvement({"status": "still_failing"}, {"status": "error"})


def test_feedback_names_the_verdict_the_failing_tests_and_the_output():
    text = build_feedback(report("still_failing").as_dict())
    assert "still_failing" in text and "tests/test_app.py::test_load_user" in text
    assert "ValueError: missing profile" in text


def test_feedback_output_is_capped():
    long = "FAILED tests/t.py::a\n" + "x" * 10_000 + "TAIL"
    text = build_feedback(report("still_failing", long).as_dict())
    assert len(text) < 4000 and text.count("TAIL") == 1


# ------------------------------------------------------------------ through the service


def flow(client, fake_llm, set_settings, monkeypatch, replies, statuses, attempts=1):
    set_settings(
        enable_github_remediation=True,
        github_token="t",
        require_remediation_approval=True,
        sandbox_enabled=True,
        sandbox_feedback_attempts=attempts,
    )
    created = []
    monkeypatch.setattr(
        remediation_service,
        "create_fix_pull_request",
        lambda **kw: created.append(kw) or PullRequestResult("https://github.com/o/r/pull/1", 1, "b", False),
    )
    queue = [report(s) for s in statuses]
    sandbox_calls = []

    def fake_sandbox(repo, path, diff, **kwargs):
        sandbox_calls.append(diff)
        return queue.pop(0)

    monkeypatch.setattr(incident_service, "run_patch_in_sandbox", fake_sandbox)
    llm = fake_llm(replies)
    body = client.post("/webhook/incident?wait=true", json=incident_payload(), headers=API_HEADERS).json()
    return body, llm, sandbox_calls


def test_the_default_is_off_and_nothing_changes(client, fake_llm, source_fetch, set_settings, monkeypatch):
    from src.config import Settings

    assert Settings().sandbox_feedback_attempts == 0
    body, llm, calls = flow(
        client, fake_llm, set_settings, monkeypatch, [make_analysis()], ["still_failing"], attempts=0
    )
    assert llm.calls == 1 and len(calls) == 1
    assert "sandbox_feedback" not in body["analysis"]


def test_a_corrected_patch_that_passes_replaces_the_first(client, fake_llm, source_fetch, set_settings, monkeypatch):
    body, llm, calls = flow(
        client,
        fake_llm,
        set_settings,
        monkeypatch,
        [make_analysis(), make_analysis(diff=SECOND_DIFF)],
        ["still_failing", "fixes_failing_tests"],
    )
    assert llm.calls == 2 and calls == [GOOD_DIFF, SECOND_DIFF]
    assert "<PREVIOUS_FEEDBACK>" in llm.prompts[1] and "tests/test_app.py::test_load_user" in llm.prompts[1]
    assert body["status"] == "awaiting_approval"
    assert body["suggested_patch"] == SECOND_DIFF
    assert body["analysis"]["sandbox"]["status"] == "fixes_failing_tests"
    (entry,) = body["analysis"]["sandbox_feedback"]
    assert entry == {
        "attempt": 1,
        "verdict_before": "still_failing",
        "verdict_after": "fixes_failing_tests",
        "outcome": "accepted",
    }
    assert body["iterations"] == 2 and len(body["analysis"]["attempts"]) == 2


def test_a_regression_can_be_repaired_instead_of_blocking(client, fake_llm, source_fetch, set_settings, monkeypatch):
    body, _, _ = flow(
        client,
        fake_llm,
        set_settings,
        monkeypatch,
        [make_analysis(), make_analysis(diff=SECOND_DIFF)],
        ["regression", "no_regression"],
    )
    assert body["status"] == "awaiting_approval" and body["suggested_patch"] == SECOND_DIFF


def test_a_patch_that_is_not_better_is_discarded(client, fake_llm, source_fetch, set_settings, monkeypatch):
    body, llm, _ = flow(
        client,
        fake_llm,
        set_settings,
        monkeypatch,
        [make_analysis(), make_analysis(diff=SECOND_DIFF)],
        ["regression", "regression"],
        attempts=3,
    )
    assert llm.calls == 2  # stops at the first non-improvement even with budget left
    assert body["suggested_patch"] == GOOD_DIFF and body["status"] == "needs_review"
    assert body["analysis"]["sandbox_feedback"][0]["outcome"] == "not_better"
    assert len(body["analysis"]["attempts"]) == 2  # the extra model call is still accounted for


def test_a_repair_that_fails_the_gate_keeps_the_first_patch(client, fake_llm, source_fetch, set_settings, monkeypatch):
    body, _, calls = flow(
        client,
        fake_llm,
        set_settings,
        monkeypatch,
        [make_analysis(), make_analysis(diff=BAD_DIFF)],
        ["still_failing"],
    )
    assert calls == [GOOD_DIFF]  # the unsound patch never reaches the sandbox
    assert body["suggested_patch"] == GOOD_DIFF
    assert body["analysis"]["sandbox_feedback"][0]["outcome"] == "gate_failed"


def test_the_budget_bounds_the_number_of_extra_passes(client, fake_llm, source_fetch, set_settings, monkeypatch):
    third = GOOD_DIFF.replace('"missing profile"', '"no profile"')
    body, llm, calls = flow(
        client,
        fake_llm,
        set_settings,
        monkeypatch,
        [make_analysis(), make_analysis(diff=SECOND_DIFF), make_analysis(diff=third)],
        ["regression", "still_failing", "partly_fixed"],
        attempts=2,
    )
    assert llm.calls == 3 and len(calls) == 3
    assert [e["outcome"] for e in body["analysis"]["sandbox_feedback"]] == ["accepted", "accepted"]
    assert body["suggested_patch"] == third


def test_a_crashing_repair_pass_does_not_lose_the_first_patch(
    client, fake_llm, source_fetch, set_settings, monkeypatch
):
    class Boom:
        def invoke(self, state):
            raise RuntimeError("boom")

    monkeypatch.setattr(incident_service, "get_repair_graph", lambda: Boom())
    body, _, _ = flow(client, fake_llm, set_settings, monkeypatch, [make_analysis()], ["still_failing"])
    assert body["suggested_patch"] == GOOD_DIFF
    assert body["analysis"]["sandbox_feedback"][0]["outcome"] == "crashed"


def test_other_verdicts_cost_nothing_extra(client, fake_llm, source_fetch, set_settings, monkeypatch):
    body, llm, calls = flow(client, fake_llm, set_settings, monkeypatch, [make_analysis()], ["partly_fixed"])
    assert llm.calls == 1 and len(calls) == 1 and "sandbox_feedback" not in body["analysis"]


def test_a_forged_feedback_key_from_the_model_is_ignored(client, fake_llm, source_fetch, set_settings, monkeypatch):
    forged = make_analysis()
    forged["sandbox_feedback"] = [{"outcome": "accepted"}]
    body, _, _ = flow(client, fake_llm, set_settings, monkeypatch, [forged], ["fixes_failing_tests"])
    assert "sandbox_feedback" not in body["analysis"]
