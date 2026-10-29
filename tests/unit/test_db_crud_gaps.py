"""Repository-level CRUD edge cases: missing rows, stale claims, idempotent create, state-guarded transitions."""

import uuid
from datetime import timedelta

import pytest

from src.db import repositories
from src.db.repositories import ClaimLostError

LEASE = timedelta(seconds=60)


def _new_incident(**overrides):
    incident_id = overrides.pop("incident_id", uuid.uuid4())
    row, created = repositories.create_incident(
        incident_id, overrides.pop("repo_name", "o/r"), "fp-1", "boom", "Traceback ...", **overrides
    )
    return incident_id, row, created


def test_get_incident_returns_none_for_an_unknown_id():
    assert repositories.get_incident(uuid.uuid4()) is None


def test_create_incident_is_idempotent_per_id():
    incident_id, first, created_first = _new_incident()
    _, second, created_second = _new_incident(incident_id=incident_id)
    assert created_first is True and created_second is False
    assert second["incident_id"] == first["incident_id"] == str(incident_id)
    assert second["status"] == "queued"


def test_update_incident_rejects_an_unknown_row():
    with pytest.raises(LookupError):
        repositories.update_incident(uuid.uuid4(), status="failed")


def test_update_incident_rejects_an_unknown_field_and_writes_nothing():
    incident_id, _, _ = _new_incident()
    with pytest.raises(AttributeError, match="unknown incident field"):
        repositories.update_incident(incident_id, status="failed", not_a_column="x")
    assert repositories.get_incident(incident_id)["status"] == "queued"


def test_update_with_a_stale_claim_token_raises_and_writes_nothing():
    incident_id, _, _ = _new_incident()
    token = repositories.claim_incident(incident_id, LEASE)
    assert token
    with pytest.raises(ClaimLostError):
        repositories.update_incident(incident_id, expected_claim_token="not-the-token", status="failed")
    assert repositories.get_incident(incident_id)["status"] == "processing"
    repositories.update_incident(incident_id, expected_claim_token=token, status="failed", status_reason="ok")
    assert repositories.get_incident(incident_id)["status"] == "failed"


def test_update_with_a_claim_token_on_a_queued_incident_is_rejected():
    incident_id, _, _ = _new_incident()
    with pytest.raises(ClaimLostError):
        repositories.update_incident(incident_id, expected_claim_token="anything", status="failed")


def test_a_second_claim_on_the_same_incident_gets_nothing():
    incident_id, _, _ = _new_incident()
    assert repositories.claim_incident(incident_id, LEASE)
    assert repositories.claim_incident(incident_id, LEASE) is None


def test_defer_incident_with_the_wrong_token_raises_and_keeps_the_claim():
    incident_id, _, _ = _new_incident()
    token = repositories.claim_incident(incident_id, LEASE)
    with pytest.raises(ClaimLostError):
        repositories.defer_incident(incident_id, "wrong-token", timedelta(seconds=30), "retry", "llm_rate_limited")
    assert repositories.get_incident(incident_id)["status"] == "processing"
    repositories.defer_incident(incident_id, token, timedelta(seconds=30), "retry", "llm_rate_limited")
    assert repositories.get_incident(incident_id)["status"] == "queued"


def test_requeue_failed_only_applies_to_failed_incidents():
    incident_id, _, _ = _new_incident()
    assert repositories.requeue_failed(incident_id, "manual") is False  # still queued
    repositories.update_incident(incident_id, status="failed")
    assert repositories.requeue_failed(incident_id, "manual") is True
    assert repositories.get_incident(incident_id)["status"] == "queued"
    assert repositories.requeue_failed(uuid.uuid4(), "manual") is False  # unknown id


@pytest.mark.parametrize(
    "analysis",
    [None, {}, {"diagnostic_chain": None}, {"diagnostic_chain": {"primary_root_cause": "not-a-dict"}}, {"x": 1}],
)
def test_root_cause_summary_tolerates_missing_or_malformed_analysis(analysis):
    assert repositories._root_cause_summary(analysis) == ""


def test_root_cause_summary_reads_the_technical_explanation():
    analysis = {"diagnostic_chain": {"primary_root_cause": {"technical_explanation": "profile is None"}}}
    assert repositories._root_cause_summary(analysis) == "profile is None"
