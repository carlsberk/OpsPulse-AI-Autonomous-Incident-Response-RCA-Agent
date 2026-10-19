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


# ------------------------------------------------------------------ v2: provider failures, safety metrics, labels


def _full(case_id, status="accepted", category="logic_error", expected_files=("a.py",), files=("a.py",), **extra):
    run = _run(case_id, status, category=category)
    run["expected"] = {
        "root_cause_category": "logic_error",
        "inconclusive": False,
        "relevant_files": list(expected_files),
        **extra.pop("expected_extra", {}),
    }
    run.update(affected_files=list(files), observed_quotes=2, grounded_quotes=2, **extra)
    return run


def test_runs_the_provider_prevented_are_excluded_from_every_rate_and_counted_apart():
    failed = {**_full("c1", "failed"), "error_category": "llm_rate_limited", "patch": ""}
    cases = per_case(_report([_full("c1"), failed]))
    assert cases["c1"]["runs"] == 1 and cases["c1"]["not_evaluated"] == 1 and cases["c1"]["accepted"] == 1
    text = render(_report([_full("c1"), failed]), None, "r.json")
    assert "not evaluated" in text and "`c1`: 1 run(s) not evaluated" in text


def test_an_accepted_patch_on_the_wrong_file_or_with_a_wrong_diagnosis_is_unsafe():
    wrong_file = _full("c1", files=("other.py",))
    wrong_diagnosis = _full("c2", category="type_error")
    safe = _full("c3")
    cases = per_case(_report([wrong_file, wrong_diagnosis, safe]))
    assert cases["c1"]["unsafe_accepted"] == 1 and cases["c2"]["unsafe_accepted"] == 1
    assert cases["c3"]["unsafe_accepted"] == 0 and cases["c2"]["false_accepted"] == 1


def test_a_correct_grounded_diagnosis_the_gate_rejected_is_a_false_rejection_not_a_false_acceptance():
    cases = per_case(_report([_full("c1", "needs_review")]))
    assert cases["c1"]["false_rejected"] == 1 and cases["c1"]["false_accepted"] == 0
    ungrounded = {**_full("c2", "needs_review"), "grounded_quotes": 1}
    assert per_case(_report([ungrounded]))["c2"]["false_rejected"] == 0  # the gate was right to refuse it


def test_accepted_alternative_categories_count_as_correct_and_are_listed_under_data_quality():
    run = _full(
        "c1",
        category="configuration_error",
        expected_extra={"alternative_categories": ["configuration_error"], "note": "judgement call"},
    )
    cases = per_case(_report([run]))
    assert cases["c1"]["correct"] == 1 and cases["c1"]["false_accepted"] == 0
    assert "ambiguous label, also accepted: configuration_error. judgement call" in render(
        _report([run]), None, "r.json"
    )


def test_small_samples_are_flagged_and_datasets_that_differ_are_not_compared():
    base = _report([_full("c1")])
    cand = {**_report([_full("c1")]), "dataset_version": "other"}
    text = render(cand, base, "r.json")
    assert "Small sample" in text and "not compared" in text
