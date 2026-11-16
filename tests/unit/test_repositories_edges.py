"""Repository edges: cursor validation, writes by a claim that lost, unknown fields, deferral, and keyset paging."""

import base64
import uuid
from datetime import timedelta

import pytest

from src.db import repositories
from src.db.repositories import ClaimLostError, InvalidCursorError

LEASE = timedelta(minutes=30)


def _new(repo="o/r", fingerprint="f" * 64):
    incident_id = uuid.uuid4()
    repositories.create_incident(incident_id, repo, fingerprint, "TypeError: x", "trace")
    return incident_id


def _b64(text):
    return base64.urlsafe_b64encode(text.encode()).decode()


@pytest.mark.parametrize(
    "cursor",
    [
        "not base64 !!",
        _b64("no separator"),
        _b64("a|b|c"),
        _b64("not-a-date|" + str(uuid.uuid4())),
        _b64("2026-01-01T00:00:00+00:00|nope"),
    ],
)
def test_a_malformed_cursor_is_an_invalid_cursor_error_not_a_crash(cursor):
    with pytest.raises(InvalidCursorError):
        repositories.list_incidents(None, None, 10, cursor)


def test_keyset_paging_visits_every_incident_exactly_once():
    ids = [_new() for _ in range(5)]
    seen, cursor = [], None
    while True:
        page, cursor = repositories.list_incidents(None, None, 2, cursor)
        seen += [row["incident_id"] for row in page]
        if cursor is None:
            break
    assert len(seen) == 5 and set(seen) == {str(i) for i in ids}  # no gaps, no repeats across pages


def test_listing_filters_by_status_and_repository():
    _new("o/a")
    _new("o/b")
    assert {r["repo_name"] for r in repositories.list_incidents(None, "o/a", 10, None)[0]} == {"o/a"}
    assert repositories.list_incidents("failed", None, 10, None)[0] == []


def test_updating_an_unknown_incident_or_field_is_refused():
    with pytest.raises(LookupError):
        repositories.update_incident(uuid.uuid4(), status="failed")
    incident_id = _new()
    with pytest.raises(AttributeError, match="unknown incident field"):
        repositories.update_incident(incident_id, not_a_column=1)


def test_a_write_by_a_claim_that_no_longer_owns_the_incident_changes_nothing():
    incident_id = _new()
    token = repositories.claim_incident(incident_id, LEASE)
    with pytest.raises(ClaimLostError):
        repositories.update_incident(incident_id, expected_claim_token="someone-else", status="failed")
    assert repositories.get_incident(incident_id)["status"] == "processing"
    repositories.update_incident(incident_id, expected_claim_token=token, status="failed")
    assert repositories.get_incident(incident_id)["status"] == "failed"
    with pytest.raises(ClaimLostError):  # the claim is spent once the incident left `processing`
        repositories.update_incident(incident_id, expected_claim_token=token, status="queued")


def test_deferring_hands_the_incident_back_but_not_before_the_delay():
    incident_id = _new()
    token = repositories.claim_incident(incident_id, LEASE)
    repositories.defer_incident(incident_id, token, timedelta(hours=1), "rate_limit: retrying", "rate_limit")
    row = repositories.get_incident(incident_id)
    assert row["status"] == "queued" and "retrying" in row["status_reason"]
    assert repositories.claim_next(LEASE) is None  # the queue honours it


def test_deferring_with_a_stale_claim_token_is_refused():
    incident_id = _new()
    repositories.claim_incident(incident_id, LEASE)
    with pytest.raises(ClaimLostError):
        repositories.defer_incident(incident_id, "stale", timedelta(seconds=5), "x", "rate_limit")


def test_a_duplicate_id_returns_the_existing_row_and_says_it_was_not_created():
    incident_id = _new()
    row, created = repositories.create_incident(incident_id, "o/r", "f" * 64, "TypeError: x", "trace")
    assert created is False and row["incident_id"] == str(incident_id)
