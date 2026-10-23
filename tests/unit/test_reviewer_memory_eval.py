"""The pattern checks of the reviewer-memory evaluation must agree with the dataset they judge."""

import pytest

from evals.dataset import load_rca_dataset
from evals.reviewer_memory_eval import CHECKS, added_lines, classify
from evals.run_rca import DATASET_FILES

CASES = {c.id: c for c in load_rca_dataset(DATASET_FILES["reviewer_memory"]).cases}


def _plus(text: str) -> str:
    return "\n".join("+" + line for line in text.splitlines())


def test_every_case_has_a_check_and_a_rejected_earlier_patch():
    assert set(CHECKS) == set(CASES)
    for case in CASES.values():
        (past,) = case.history
        assert past.reviewer_decision == "rejected" and past.reviewer_note and past.suggested_patch


@pytest.mark.parametrize("case_id", sorted(CASES))
def test_the_rejected_style_is_recognised_as_the_rejected_thing(case_id):
    """The earlier, rejected patch is the naive fix; the check must call a patch of that style 'rejected'."""
    rejected = CASES[case_id].history[0].suggested_patch
    assert classify(case_id, rejected) in ("did_rejected_thing", "other_patch")
    # the checks are written for the *current* function's names, so translate the old names to the new ones
    renamed = {
        "memory-config-default": rejected.replace("database_url", "cache_ttl"),
        "memory-ratio-none": rejected,
        "memory-lookup-raise": rejected,
    }[case_id]
    assert classify(case_id, renamed) == "did_rejected_thing"


@pytest.mark.parametrize("case_id", sorted(CASES))
def test_the_scripted_fix_that_follows_the_note_is_recognised_as_following_it(case_id):
    fix = CASES[case_id].mock_replies[0]["fix"]
    assert classify(case_id, _plus(fix["new"])) == "followed_note"


def test_no_patch_and_unrelated_patches_are_told_apart():
    assert classify("memory-config-default", None) == "no_patch"
    assert classify("memory-config-default", "") == "no_patch"
    assert classify("memory-config-default", "+    x = 1\n") == "other_patch"


def test_only_added_lines_count_so_a_removed_default_is_not_the_rejected_thing():
    patch = '-    return config.get("cache_ttl", 60)\n+    raise KeyError("cache_ttl")\n'
    assert 'get("cache_ttl"' not in added_lines(patch)
    assert classify("memory-config-default", patch) == "followed_note"
