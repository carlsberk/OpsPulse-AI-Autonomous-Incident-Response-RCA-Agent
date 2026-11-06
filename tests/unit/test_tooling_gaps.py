"""Command-line entry points and report formatting that were not exercised: the eval runners, the review-outcome
report, the incident CLI's HTTP transport and its text formatters.

Everything here is offline and writes only to tmp_path."""

import io
import json
import urllib.error
import urllib.request
from types import SimpleNamespace

import pytest

from evals import repro_eval, reviewer_memory_eval, run_rca, run_retrieval
from evals.dataset import load_rca_dataset
from src import cli
from src.cli import ApiError, format_answer, format_incident, main
from src.services import review_outcomes

# ------------------------------------------------------------------ evals.run_rca


def test_run_rca_mock_writes_a_json_and_markdown_report(tmp_path, capsys):
    code = run_rca.main(["--mode", "mock", "--dataset", "holdout", "--case", "ho-none-config", "--out", str(tmp_path)])
    assert code == 0
    report = json.loads(next(tmp_path.glob("rca-holdout-mock-*.json")).read_text(encoding="utf-8"))
    assert [r["case_id"] for r in report["results"]] == ["ho-none-config"]
    assert next(tmp_path.glob("rca-holdout-mock-*.md")).read_text(encoding="utf-8").strip()
    assert "reports:" in capsys.readouterr().out


def test_run_rca_fails_with_exit_code_1_when_a_minimum_is_not_met(tmp_path, capsys):
    argv = ["--mode", "mock", "--case", "ho-none-config", "--dataset", "holdout", "--out", str(tmp_path)]
    assert run_rca.main([*argv, "--min", "category_accuracy=1.5"]) == 1
    assert "THRESHOLD FAILED: category_accuracy=1.0 < 1.5" in capsys.readouterr().err
    assert run_rca.main([*argv, "--min", "category_accuracy=0.5"]) == 0


def test_run_rca_rescore_recomputes_metrics_without_running_the_model(tmp_path, capsys):
    run_rca.main(["--mode", "mock", "--dataset", "holdout", "--case", "ho-none-config", "--out", str(tmp_path)])
    saved = next(tmp_path.glob("rca-holdout-mock-*.json"))
    data = json.loads(saved.read_text(encoding="utf-8"))
    data["metrics"] = {}  # stale definitions
    saved.write_text(json.dumps(data), encoding="utf-8")
    assert run_rca.main(["--rescore", str(saved)]) == 0
    rescored = json.loads(saved.read_text(encoding="utf-8"))
    assert rescored["metrics"]["cases"] and "rescored_at" in rescored


def test_run_rca_accepts_an_external_dataset_file(tmp_path, monkeypatch):
    monkeypatch.setattr(run_rca, "DATASET_FILES", dict(run_rca.DATASET_FILES))  # main() registers the file globally
    external = tmp_path / "pilot.json"
    external.write_text(run_rca.DATASET_FILES["holdout"].read_text(encoding="utf-8"), encoding="utf-8")
    code = run_rca.main(["--dataset-file", str(external), "--case", "ho-none-config", "--out", str(tmp_path)])
    assert code == 0 and list(tmp_path.glob("rca-pilot-mock-*.json"))


def test_run_rca_live_mode_refuses_to_start_without_a_key(set_settings):
    set_settings(groq_api_key="")
    with pytest.raises(SystemExit, match="GROQ_API_KEY"):
        run_rca.main(["--mode", "live"])


def test_check_minimums_treats_a_missing_metric_as_a_failure():
    assert run_rca._check_minimums({"x": {"value": 0.9}}, ["x=0.8", "y=0.1"]) == ["y=None < 0.1"]


# ------------------------------------------------------------------ evals.run_retrieval


def test_run_retrieval_writes_a_report_and_exits_zero_without_leaks(tmp_path, capsys):
    assert run_retrieval.main(["--out", str(tmp_path), "--k", "2"]) == 0
    report = json.loads(next(tmp_path.glob("retrieval-*.json")).read_text(encoding="utf-8"))
    assert report["k"] == 2 and "lexical-v1" in report["strategies"]
    out = capsys.readouterr().out
    assert "| strategy |" in out and "per query (lexical-v1)" in out


def test_run_retrieval_exits_one_when_any_strategy_leaks_across_repositories(tmp_path, monkeypatch):
    leaky = {"recall@3": 0, "precision": 0, "mrr": 0, "empty_ok": 0, "leaks": 1, "duplicates": 0, "per_query": []}
    monkeypatch.setattr(run_retrieval, "evaluate_strategy", lambda name, data, k: leaky)
    assert run_retrieval.main(["--out", str(tmp_path)]) == 1


# ------------------------------------------------------------------ evals.reviewer_memory_eval


def test_reviewer_memory_mock_run_compares_notes_on_and_off_and_restores_the_setting(tmp_path, set_settings, capsys):
    from src.config import settings

    set_settings(history_reviewer_notes=True)
    out = tmp_path / "memory.json"
    assert (
        reviewer_memory_eval.main(["--mode", "mock", "--repeat", "1", "--case", "memory-ratio-none", "--out", str(out)])
        == 0
    )
    report = json.loads(out.read_text(encoding="utf-8"))
    assert {row["notes"] for row in report["rows"]} == {"on", "off"}
    assert set(report["counts"]) == {"on", "off"}
    assert settings.history_reviewer_notes is True  # the A/B switch is put back


def test_reviewer_memory_live_mode_refuses_to_start_without_a_key(tmp_path, set_settings):
    set_settings(groq_api_key="")
    with pytest.raises(SystemExit, match="GROQ_API_KEY"):
        reviewer_memory_eval.main(["--mode", "live", "--out", str(tmp_path / "x.json")])


# ------------------------------------------------------------------ evals.repro_eval


def _final(**overrides):
    base = {
        "workflow_status": "accepted",
        "root_cause_analysis": {},
        "suggested_patch": "--- a/x.py\n+++ b/x.py\n",
        "affected_file": "x.py",
        "error_message": "ValueError: missing profile",
    }
    return {**base, **overrides}


@pytest.fixture
def case():
    return load_rca_dataset(run_rca.DATASET_FILES["holdout"]).cases[0]


@pytest.mark.parametrize(
    ("final", "outcome"),
    [
        (_final(workflow_status="failed"), "not_evaluated"),
        (_final(suggested_patch=None), "no_patch"),
        (_final(affected_file=None), "no_patch"),
        (_final(root_cause_analysis={"reproduction_test": "not a dict"}), "no_test"),
        (_final(root_cause_analysis={}), "no_test"),
    ],
    ids=["provider-failure", "no-patch", "no-target", "malformed-test", "no-test"],
)
def test_repro_eval_classifies_cases_that_never_reach_the_sandbox(monkeypatch, case, final, outcome):
    monkeypatch.setattr(repro_eval, "run_final", lambda c, factory: (final, 7))
    monkeypatch.setattr(repro_eval, "run_patch_in_sandbox", lambda *a, **k: pytest.fail("sandbox must not run"))
    row = repro_eval.evaluate_case(case)
    assert row["outcome"] == outcome and row["wall_ms"] == 7 and row["case_id"] == case.id


def test_repro_eval_reports_the_sandbox_verdict_on_the_models_own_test(monkeypatch, case):
    seen = {}
    test = {"path": "tests/test_repro.py", "content": "def test_x(): ..."}
    verdict = SimpleNamespace(
        status="reproduced_and_fixed",
        summary="raised before, passed after",
        before=SimpleNamespace(output_tail="x" * 900),
    )

    def sandbox(repo, target, patch, snapshot, reproduction):
        seen["spec"] = reproduction
        return SimpleNamespace(status="passed", summary="ok", reproduction=verdict)

    monkeypatch.setattr(
        repro_eval, "run_final", lambda c, f: (_final(root_cause_analysis={"reproduction_test": test}), 3)
    )
    monkeypatch.setattr(repro_eval, "run_patch_in_sandbox", sandbox)
    row = repro_eval.evaluate_case(case)
    assert row["outcome"] == "reproduced_and_fixed" and len(row["before_tail"]) == 400
    assert (seen["spec"].path, seen["spec"].expected_error) == ("tests/test_repro.py", "ValueError")


def test_repro_eval_falls_back_to_the_sandbox_status_when_no_reproduction_ran(monkeypatch, case):
    test = {"path": "tests/test_repro.py", "content": "x"}
    monkeypatch.setattr(
        repro_eval, "run_final", lambda c, f: (_final(root_cause_analysis={"reproduction_test": test}), 3)
    )
    monkeypatch.setattr(
        repro_eval,
        "run_patch_in_sandbox",
        lambda *a, **k: SimpleNamespace(status="error", summary="image has no pytest", reproduction=None),
    )
    row = repro_eval.evaluate_case(case)
    assert row["outcome"] == "sandbox_error" and row["summary"] == "image has no pytest"


def test_repro_eval_snapshot_writes_the_cases_own_files_into_the_sandbox_dir(tmp_path, case):
    sha = repro_eval._snapshot_of(case)("o/r", tmp_path / "snap", 10_000)
    assert sha == "0" * 40
    for name, text in case.files.items():
        assert (tmp_path / "snap" / name).read_text(encoding="utf-8") == text


def test_repro_eval_main_needs_a_key_then_writes_counts(tmp_path, monkeypatch, set_settings, case):
    set_settings(groq_api_key="", sandbox_image=repro_eval.settings.sandbox_image)
    with pytest.raises(SystemExit, match="GROQ_API_KEY"):
        repro_eval.main(["--out", str(tmp_path / "r.json")])
    set_settings(groq_api_key="test-key")
    monkeypatch.setattr(repro_eval, "evaluate_case", lambda c: {"case_id": c.id, "outcome": "no_patch"})
    out = tmp_path / "r.json"
    code = repro_eval.main(
        ["--dataset", "holdout", "--case", case.id, "--sleep", "0", "--image", "img", "--out", str(out)]
    )
    report = json.loads(out.read_text(encoding="utf-8"))
    assert code == 0 and report["counts"] == {"no_patch": 1} and report["dataset"] == "holdout"


# ------------------------------------------------------------------ review outcomes report


def _row(status, note="", score=0.9, prompt="v7"):
    return {
        "prompt_version": prompt, "evaluator_version": "g1", "model": "m", "category": "null_reference",
        "quality_score": score, "approval_status": status, "note": note, "reviewer": "bob",
    }  # fmt: skip


def test_review_outcomes_main_prints_or_writes_the_report(tmp_path, monkeypatch, capsys):
    rows = [_row("approved"), _row("rejected", note="use a guard, not a default"), _row("pending")]
    monkeypatch.setattr(review_outcomes, "collect", lambda: rows)
    assert review_outcomes.main([]) == 0
    printed = capsys.readouterr().out
    assert "# Review outcomes" in printed and "use a guard, not a default" in printed
    target = tmp_path / "outcomes.md"
    assert review_outcomes.main(["--out", str(target)]) == 0
    assert target.read_text(encoding="utf-8").strip() == printed.strip()
    assert capsys.readouterr().out == ""  # with --out nothing is printed


def test_review_outcomes_claims_no_approval_rate_below_the_minimum(monkeypatch):
    text = review_outcomes.summarize([_row("approved"), _row("rejected")])
    assert "no approval rate is claimed" in text and "| n/a |" in text


# ------------------------------------------------------------------ incident CLI: transport and formatters


class _Response:
    status = 200

    def __init__(self, body=b'{"ok": true}'):
        self._body = body

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def read(self):
        return self._body


def test_urllib_transport_returns_status_and_body(monkeypatch):
    seen = {}

    def urlopen(request, timeout):
        seen.update(url=request.full_url, method=request.get_method(), timeout=timeout)
        return _Response()

    monkeypatch.setattr(urllib.request, "urlopen", urlopen)
    assert cli._urllib_transport("GET", "http://x/healthz", {}, None) == (200, b'{"ok": true}')
    assert seen == {"url": "http://x/healthz", "method": "GET", "timeout": 120}


def test_urllib_transport_turns_an_http_error_into_status_and_body(monkeypatch):
    def urlopen(request, timeout):
        raise urllib.error.HTTPError(request.full_url, 404, "nf", {}, io.BytesIO(b'{"error": {"message": "nope"}}'))

    monkeypatch.setattr(urllib.request, "urlopen", urlopen)
    status, body = cli._urllib_transport("GET", "http://x/incidents/1", {}, None)
    assert status == 404 and b"nope" in body


@pytest.mark.parametrize("url", ["file:///etc/passwd", "ftp://x/y", "gopher://x"])
def test_urllib_transport_refuses_non_http_schemes(url):
    with pytest.raises(ValueError, match="http:// or https://"):
        cli._urllib_transport("GET", url, {}, None)


def _recording(responses):
    calls = []

    def transport(method, url, headers, body):
        calls.append((method, url, json.loads(body) if body else None))
        status, payload = responses.pop(0)  # scripted replies, in request order
        return status, json.dumps(payload).encode()

    return transport, calls


def test_submit_sends_the_trace_the_id_and_the_wait_flag(tmp_path, capsys):
    trace = tmp_path / "trace.txt"
    trace.write_text("Traceback ...", encoding="utf-8")
    transport, calls = _recording([(202, {"incident_id": "abc", "status": "queued"})])
    code = main(
        ["--url", "http://x", "submit", "--repo", "o/r", "--error", "boom", "--trace-file", str(trace), "--id", "abc"],
        transport=transport,
    )
    assert code == 0 and "accepted: abc  status: queued" in capsys.readouterr().out
    sent = {"repo_name": "o/r", "error_message": "boom", "stack_trace": "Traceback ...", "incident_id": "abc"}
    assert calls == [("POST", "http://x/webhook/incident", sent)]


def test_submit_reads_the_error_from_standard_input(monkeypatch, capsys):
    monkeypatch.setattr("sys.stdin", io.StringIO("KeyError: 'x'\n"))
    transport, calls = _recording([(202, {"incident_id": "i1", "status": "queued"})])
    assert main(["--url", "http://x", "submit", "--repo", "o/r", "--error-file", "-"], transport=transport) == 0
    assert calls[0][2]["error_message"] == "KeyError: 'x'\n"


def test_list_mentions_the_cursor_when_more_rows_exist(capsys):
    item = {
        "incident_id": "i1",
        "status": "needs_review",
        "quality_score": 0.5,
        "repo_name": "o/r",
        "affected_file": None,
    }
    transport, _ = _recording([(200, {"items": [item], "next_cursor": "CUR"})])
    assert main(["--url", "http://x", "list", "--limit", "1"], transport=transport) == 0
    out = capsys.readouterr().out
    assert "needs review" in out and "(more available; cursor CUR)" in out


def test_retry_command_reports_the_requeued_incident(capsys):
    transport, calls = _recording([(200, {"incident_id": "i1", "status": "queued"})])
    assert main(["--url", "http://x", "retry", "i1"], transport=transport) == 0
    assert calls[0][:2] == ("POST", "http://x/incidents/i1/retry")
    assert "re-queued: i1  status: queued" in capsys.readouterr().out


def test_demo_command_hands_over_to_the_demo_console(monkeypatch):
    from scripts import demo_console

    monkeypatch.setattr(demo_console, "main", lambda argv: 7)
    assert main(["demo"]) == 7


def test_format_incident_shows_the_pull_request_and_a_pending_approval():
    text = format_incident(
        {
            "incident_id": "i1",
            "status": "awaiting_approval",
            "pr_url": "https://gh/pr/1",
            "pending_approval": {"id": "a"},
        }
    )
    assert "https://gh/pr/1" in text and "waiting for a reviewer's decision" in text


@pytest.mark.parametrize(
    ("answer", "expected"),
    [
        ({"answer": "a", "degraded": True}, "model unavailable"),
        ({"answer": "a", "answerable": False}, "not in the record"),
        ({"answer": "a", "grounded": False}, "NOT GROUNDED - treat with caution"),
        ({"answer": "a", "unverified_quotes": ["made up"]}, "not in the record: made up"),
        ({"answer": "a", "cited_sections": ["root cause"]}, "sections: root cause"),
    ],
    ids=["degraded", "unanswerable", "ungrounded", "unverified-quote", "citations"],
)
def test_format_answer_flags_every_reason_to_distrust_an_answer(answer, expected):
    assert expected in format_answer(answer)


def test_api_error_carries_the_status():
    assert ApiError(503, "down").status == 503
