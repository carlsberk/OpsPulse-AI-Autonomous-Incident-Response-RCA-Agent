"""The evaluation framework itself: dataset validation, metric arithmetic, deterministic mock runs."""

import json

import pytest
from pydantic import ValidationError

from evals.dataset import RCADataset, load_rca_dataset
from evals.harness import ScriptedModel, build_scripted_reply, run_case
from evals.metrics import compute_metrics
from evals.run_rca import _check_minimums
from evals.run_retrieval import DATASET as RETRIEVAL_DATASET
from evals.run_retrieval import evaluate_strategy
from src.agent.patching import apply_unified_diff
from src.agent.schemas import RCAOutput


def _result(
    expected_cat, predicted, inconclusive=False, gate=True, insufficient=False, quotes=(2, 2), files=1, attempts=None
):
    return {
        "expected": {"root_cause_category": expected_cat, "inconclusive": inconclusive, "relevant_files": ["a.py"]},
        "predicted_category": predicted,
        "gate_passed": gate,
        "declared_insufficient": insufficient,
        "observed_quotes": quotes[0],
        "grounded_quotes": quotes[1],
        "affected_files": ["a.py"] * files,
        "grounded_files": files,
        "workflow_status": "accepted" if gate else "needs_review",
        "wall_ms": 10,
        "attempts": attempts or [{"schema_valid": True, "latency_ms": 100, "input_tokens": 1000, "output_tokens": 200}],
    }


def test_dataset_loads_and_has_required_coverage():
    data = load_rca_dataset()
    assert 20 <= len(data.cases) <= 50
    assert sum(c.expected.inconclusive for c in data.cases) >= 3
    ids = {c.id for c in data.cases}
    assert {"injection-in-log", "injection-in-history", "missing-source", "message-only"} <= ids


def test_dataset_rejects_unknown_categories_and_duplicate_ids():
    case = load_rca_dataset().cases[0].model_dump()
    with pytest.raises(ValidationError):
        RCADataset.model_validate({"version": "x", "cases": [case, case]})
    case["expected"]["root_cause_category"] = "cosmic_rays"
    with pytest.raises(ValidationError):
        RCADataset.model_validate({"version": "x", "cases": [case]})


def test_scripted_replies_are_schema_valid_and_patches_apply():
    for case in load_rca_dataset().cases:
        for spec in case.mock_replies:
            if "raw" in spec:
                continue
            reply = json.loads(build_scripted_reply(case, spec))
            RCAOutput.model_validate(reply)
            diff = reply["patch_remediation"]["unified_diff"]
            if diff and not spec.get("corrupt_diff"):
                target = reply["affected_files"][0]
                assert apply_unified_diff(case.files[target], diff) != case.files[target], case.id


def test_metric_arithmetic():
    results = [
        _result("missing_key", "missing_key"),
        _result("type_error", "missing_key"),  # accepted with the wrong category
        _result("unknown", "unknown", inconclusive=True, gate=False, insufficient=True, quotes=(1, 0)),
    ]
    m = compute_metrics(results, cost_in_per_mtok=1.0, cost_out_per_mtok=2.0)
    assert m["category_accuracy"]["value"] == 0.5
    assert m["false_acceptance_rate"]["value"] == 0.5
    assert m["abstention_recall"]["value"] == 1.0
    assert m["evidence_grounding_accuracy"] == {**m["evidence_grounding_accuracy"], "numerator": 4, "denominator": 5}
    assert m["unsupported_claim_rate"]["numerator"] == 1 and m["unsupported_claim_rate"]["denominator"] == 8
    assert m["tokens"] == {"input": 3000, "output": 600, "attempts_with_usage": 3}
    assert m["estimated_cost_usd"] == pytest.approx(0.0042)


def test_empty_denominators_are_reported_as_none_not_zero():
    m = compute_metrics([_result("x", "x")])
    assert m["abstention_recall"]["value"] is None and m["abstention_recall"]["denominator"] == 0
    assert compute_metrics([_result("x", "x")])["estimated_cost_usd"] is None  # no prices configured


def test_thresholds():
    metrics = {"category_accuracy": {"value": 0.8}, "abstention_recall": {"value": None}}
    assert _check_minimums(metrics, ["category_accuracy=0.7"]) == []
    assert _check_minimums(metrics, ["category_accuracy=0.9", "abstention_recall=0.5"]) == [
        "category_accuracy=0.8 < 0.9",
        "abstention_recall=None < 0.5",
    ]


def test_mock_run_is_deterministic_and_exercises_the_gate():
    data = {c.id: c for c in load_rca_dataset().cases}

    def run(case_id):
        model = ScriptedModel(data[case_id])
        return run_case(data[case_id], lambda temperature: model)

    def stable(result):
        """Everything except measured time: `wall_ms` and each attempt's `latency_ms` differ by a millisecond on a slow
        runner (this test failed on a macOS CI job for exactly that)."""
        kept = {k: v for k, v in result.items() if k != "wall_ms"}
        kept["attempts"] = [{k: v for k, v in a.items() if k != "latency_ms"} for a in result["attempts"]]
        return kept

    first, second = run("misleading-trace"), run("misleading-trace")
    assert stable(first) == stable(second)
    assert first["gate_passed"] and len(first["attempts"]) == 2  # fabricated quote rejected, then corrected
    injection = run("injection-in-log")
    assert injection["gate_passed"] and injection["affected_files"] == ["app/refunds.py"]
    assert not run("ambiguous-worker-crash")["gate_passed"]


def test_retrieval_evaluation_never_leaks_between_repositories():
    data = json.loads(RETRIEVAL_DATASET.read_text(encoding="utf-8"))
    for strategy in ("fingerprint-or-file-v0", "lexical-v1"):
        assert evaluate_strategy(strategy, data, 3)["leaks"] == 0


def test_provider_failures_are_excluded_from_quality_metrics_and_listed():
    ok = _result("missing_key", "missing_key")
    limited = {
        **_result("type_error", None),
        "case_id": "x",
        "workflow_status": "failed",
        "error_category": "llm_rate_limited",
        "gate_passed": False,
    }
    m = compute_metrics([ok, limited])
    assert m["cases_not_evaluated"] == ["x"]
    assert m["category_accuracy"]["denominator"] == 1 and m["category_accuracy"]["value"] == 1.0
    assert m["workflow_failure_rate"]["numerator"] == 1  # still visible as an operational failure


def test_holdout_set_is_valid_and_disjoint_from_the_tuning_set():
    from evals.run_rca import DATASET_FILES

    tuning = load_rca_dataset(DATASET_FILES["tuning"])
    holdout = load_rca_dataset(DATASET_FILES["holdout"])
    assert len(holdout.cases) >= 10 and sum(c.expected.inconclusive for c in holdout.cases) >= 2
    assert not {c.id for c in tuning.cases} & {c.id for c in holdout.cases}
    # Standard messages (e.g. IndexError) may repeat; the same incident (message + trace) must not.
    assert not {(c.error_message, c.stack_trace) for c in tuning.cases} & {
        (c.error_message, c.stack_trace) for c in holdout.cases
    }
