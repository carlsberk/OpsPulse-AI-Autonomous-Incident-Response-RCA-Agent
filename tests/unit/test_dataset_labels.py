"""Ambiguous labels: a case may list other categories a careful reviewer would accept, and the metrics honour that."""

import pytest
from pydantic import ValidationError

from evals.dataset import Expected, load_rca_dataset
from evals.metrics import category_matches
from evals.run_rca import DATASET_FILES


def test_alternatives_must_be_known_categories_and_not_repeat_the_primary():
    with pytest.raises(ValidationError):
        Expected(root_cause_category="logic_error", inconclusive=False, alternative_categories=["not_a_category"])
    with pytest.raises(ValidationError):
        Expected(root_cause_category="logic_error", inconclusive=False, alternative_categories=["logic_error"])
    expected = Expected(root_cause_category="logic_error", inconclusive=False, alternative_categories=["type_error"])
    assert expected.accepts("logic_error") and expected.accepts("type_error") and not expected.accepts("missing_key")


def test_metrics_treat_an_accepted_alternative_as_correct():
    result = {
        "predicted_category": "configuration_error",
        "expected": {
            "root_cause_category": "dependency_unavailable",
            "alternative_categories": ["configuration_error"],
        },
    }
    assert category_matches(result)
    result["predicted_category"] = "missing_key"
    assert not category_matches(result)
    assert category_matches({"predicted_category": "x", "expected": {"root_cause_category": "x"}})  # old reports


def test_the_ambiguous_holdout_case_carries_its_reasoning():
    cases = {c.id: c for c in load_rca_dataset(DATASET_FILES["holdout"]).cases}
    expected = cases["ho-pool-timeout"].expected
    assert expected.alternative_categories == ["configuration_error"] and "PoolTimeout" in expected.note
    assert not cases["ho-drift-attribute"].expected.alternative_categories  # clear label: it is a measurement problem
