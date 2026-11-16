"""Every service entry point turns a database failure into DatabaseUnavailableError (the API maps it to 503), never a
raw SQLAlchemy error, and nothing about the failure leaks into the message."""

import uuid

import pytest
from sqlalchemy.exc import OperationalError

from src.db import repositories
from src.services import incident_service
from src.services.incident_service import DatabaseUnavailableError, IncidentConflictError, IncidentNotFoundError

ID = uuid.uuid4()
BOOM = OperationalError("SELECT secret FROM t", {}, Exception("password=hunter2"))


def _down(*args, **kwargs):
    raise BOOM


CALLS = [
    ("list_incidents", lambda: incident_service.list_incidents(None, None, 10, None), "list_incidents"),
    ("get_incident_with_approval", lambda: incident_service.get_incident_with_approval(ID), "get_incident"),
    ("get_audit", lambda: incident_service.get_audit(ID), "get_incident"),
    ("claim_for_inline_run", lambda: incident_service.claim_for_inline_run(ID), "claim_incident"),
    ("retry_incident", lambda: incident_service.retry_incident(ID, "alice"), "get_incident"),
    ("reopen_approval", lambda: incident_service.reopen_approval(ID, "alice"), "get_incident"),
    ("persist", lambda: incident_service._persist_or_raise(ID, status="x"), "update_incident"),
]


@pytest.mark.parametrize(("name", "call", "failing"), CALLS, ids=[c[0] for c in CALLS])
def test_a_database_failure_becomes_database_unavailable_without_leaking_details(monkeypatch, name, call, failing):
    monkeypatch.setattr(repositories, failing, _down)
    with pytest.raises(DatabaseUnavailableError) as caught:
        call()
    assert str(caught.value) == "database unavailable"
    assert "secret" not in str(caught.value) and "hunter2" not in str(caught.value)


def test_retrying_an_unknown_incident_is_not_found(monkeypatch):
    monkeypatch.setattr(repositories, "get_incident", lambda _id: None)
    with pytest.raises(IncidentNotFoundError):
        incident_service.retry_incident(ID, "alice")


def test_only_failed_incidents_can_be_retried(monkeypatch):
    monkeypatch.setattr(repositories, "get_incident", lambda _id: {"status": "analysis_ready"})
    monkeypatch.setattr(repositories, "requeue_failed", lambda *_: False)
    with pytest.raises(IncidentConflictError, match="only failed"):
        incident_service.retry_incident(ID, "alice")


def test_a_row_that_vanishes_between_write_and_read_is_reported_not_returned_as_none(monkeypatch):
    monkeypatch.setattr(repositories, "get_incident", lambda _id: None)
    with pytest.raises(DatabaseUnavailableError, match="disappeared"):
        incident_service._load(ID)


def test_a_missing_incident_has_no_approval_lookup(monkeypatch):
    monkeypatch.setattr(repositories, "get_incident", lambda _id: None)
    monkeypatch.setattr(repositories, "get_pending_approval", lambda _id: pytest.fail("must not look up approvals"))
    assert incident_service.get_incident_with_approval(ID) is None
    assert incident_service.get_audit(ID) is None
