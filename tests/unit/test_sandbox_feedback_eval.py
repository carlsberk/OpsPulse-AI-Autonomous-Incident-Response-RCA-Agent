"""The feedback evaluation: first attempt, real-test verdict, one repair round, with every external part faked."""

import json
import pathlib
from types import SimpleNamespace

from evals import sandbox_feedback_eval as ev

OUT = "FAILED tests/test_x.py::test_bug - AssertionError\nE   AssertionError: still wrong"


def final_state(gate=True, patch="PATCH1", tokens=(10, 5)):
    return {
        "workflow_status": "accepted" if gate else "needs_review",
        "quality_gate_passed": gate,
        "suggested_patch": patch,
        "attempts": [{"input_tokens": tokens[0], "output_tokens": tokens[1]}],
    }


def install(monkeypatch, first, repaired, verdicts):
    seen = {"states": [], "verified": []}
    monkeypatch.setattr(ev, "run_final", lambda case, factory: (first, 1))

    class Repair:
        def invoke(self, state):
            seen["states"].append(state)
            return repaired

    monkeypatch.setattr(ev, "build_repair_graph", lambda: Repair())
    queue = list(verdicts)

    def verify(repo, python, meta, patch):
        seen["verified"].append(patch)
        verdict = queue.pop(0)
        return verdict, f"detail {verdict}", OUT if verdict == "fails_tests" else ""

    monkeypatch.setattr(ev, "verify_patch_detailed", verify)
    return seen


def test_a_repair_round_that_turns_the_real_test_green(monkeypatch):
    seen = install(
        monkeypatch, final_state(), final_state(patch="PATCH2", tokens=(30, 9)), ["fails_tests", "passes_tests"]
    )
    row = ev.evaluate_case(SimpleNamespace(id="hist-1"), {}, pathlib.Path("."), "docker:x", 1)
    assert (row["first_verdict"], row["final_verdict"]) == ("fails_tests", "passes_tests")
    assert seen["verified"] == ["PATCH1", "PATCH2"]
    assert "tests/test_x.py::test_bug" in seen["states"][0]["previous_feedback"]
    assert row["rounds"] == [{"gate_passed": True, "verdict": "passes_tests", "detail": "detail passes_tests"}]
    assert row["tokens_in_out"] == [30, 9]


def test_a_passing_first_attempt_costs_nothing_more(monkeypatch):
    seen = install(monkeypatch, final_state(), final_state(), ["passes_tests"])
    row = ev.evaluate_case(SimpleNamespace(id="c"), {}, pathlib.Path("."), "docker:x", 1)
    assert row["rounds"] == [] and seen["states"] == []


def test_no_repair_when_the_gate_rejected_the_first_patch_or_there_is_no_patch(monkeypatch):
    seen = install(monkeypatch, final_state(gate=False), final_state(), ["fails_tests"])
    assert ev.evaluate_case(SimpleNamespace(id="c"), {}, pathlib.Path("."), "docker:x", 1)["rounds"] == []
    install(monkeypatch, final_state(patch=None), final_state(), [])
    row = ev.evaluate_case(SimpleNamespace(id="c"), {}, pathlib.Path("."), "docker:x", 1)
    assert row["first_verdict"] == "no_patch" and not seen["states"]


def test_a_repair_with_the_same_or_a_rejected_patch_is_not_tested_again(monkeypatch):
    install(monkeypatch, final_state(), final_state(patch="PATCH1"), ["fails_tests"])
    row = ev.evaluate_case(SimpleNamespace(id="c"), {}, pathlib.Path("."), "docker:x", 3)
    assert row["rounds"] == [{"gate_passed": True, "verdict": "no_new_patch"}]
    install(monkeypatch, final_state(), final_state(gate=False, patch="PATCH2"), ["fails_tests"])
    row = ev.evaluate_case(SimpleNamespace(id="c"), {}, pathlib.Path("."), "docker:x", 3)
    assert row["rounds"][0]["verdict"] == "no_new_patch" and row["final_verdict"] == "fails_tests"


def test_main_writes_a_report_and_needs_a_key(monkeypatch, tmp_path, capsys, set_settings):
    dataset = tmp_path / "h.json"
    dataset.write_text("{}")
    dataset.with_suffix(".meta.json").write_text(json.dumps({"hist-1": {}}))
    argv = ["--dataset", str(dataset), "--repo-dir", str(tmp_path), "--python", "docker:x", "--out", str(tmp_path)]
    set_settings(groq_api_key="")
    import pytest

    with pytest.raises(SystemExit):
        ev.main(argv)
    set_settings(groq_api_key="k")
    monkeypatch.setattr(ev, "load_rca_dataset", lambda path: SimpleNamespace(cases=[SimpleNamespace(id="hist-1")]))
    install(monkeypatch, final_state(), final_state(patch="PATCH2"), ["fails_tests", "passes_tests"])
    monkeypatch.setattr(ev.time, "sleep", lambda s: None)
    assert ev.main(argv) == 0
    assert "fails_tests -> passes_tests" in capsys.readouterr().out
    (written,) = tmp_path.glob("sandbox-feedback-*.json")
    assert json.loads(written.read_text())["summary"] == {"fails_tests -> passes_tests": 1}
