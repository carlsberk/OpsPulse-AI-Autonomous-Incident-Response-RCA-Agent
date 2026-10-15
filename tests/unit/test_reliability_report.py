from evals.reliability_report import case_class, changes, per_case, per_class, render


def _run(case_id, status, category="logic_error", patch="x", expected="logic_error", wall=10):
    return {
        "case_id": case_id,
        "workflow_status": status,
        "predicted_category": category,
        "patch": patch,
        "expected": {"root_cause_category": expected, "inconclusive": False},
        "wall_ms": wall,
        "attempts": [{"input_tokens": 3, "output_tokens": 2}],
    }


def _report(runs, prompt="p"):
    return {
        "dataset_version": "d",
        "mode": "live",
        "started_at": "t",
        "run_metadata": {"prompt_version": prompt},
        "results": runs,
    }


def test_classes_and_counts():
    ids = ("control-x", "caller-y", "adv-z", "other")
    assert [case_class(c) for c in ids] == ["control", "caller", "adversarial", "other"]
    runs = [_run("caller-a", "needs_review", patch=""), _run("caller-a", "accepted"), _run("control-b", "accepted")]
    cases = per_case(_report(runs))
    assert cases["caller-a"]["accepted"] == 1 and cases["caller-a"]["patch_kept"] == 1
    assert cases["caller-a"]["stable"] is False and cases["control-b"]["tokens"] == 5
    assert per_class(cases)["caller"]["runs"] == 2


def test_wrong_category_acceptance_counts_as_false_acceptance():
    cases = per_case(_report([_run("caller-a", "accepted", category="type_error")]))
    assert cases["caller-a"]["false_accepted"] == 1


def test_changes_lists_only_cases_that_moved():
    base = _report([_run("caller-a", "accepted"), _run("control-b", "accepted")], "v5")
    cand = _report([_run("caller-a", "needs_review", patch=""), _run("control-b", "accepted")], "v6")
    moved = changes(per_case(base), per_case(cand))
    assert len(moved) == 1 and "caller-a" in moved[0] and "patch kept 1/1 -> 0/1" in moved[0]
    section = render(cand, base, "cand.json").split("## Change")[1]
    assert "`v5`" in section and "caller-a" in section and "control-b" not in section
