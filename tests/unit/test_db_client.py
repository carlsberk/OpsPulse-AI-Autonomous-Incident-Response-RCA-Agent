"""Database client: URL normalisation, transaction semantics, and refusing to run without configuration."""

import uuid

import pytest

from src.config import ConfigError
from src.db import repositories
from src.db.client import get_engine, normalize_database_url, session_scope
from src.db.models import Incident


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("postgres://u:p@h/db", "postgresql+psycopg2://u:p@h/db"),
        ("postgresql://u:p@h/db", "postgresql+psycopg2://u:p@h/db"),
        ("postgresql+psycopg2://u:p@h/db", "postgresql+psycopg2://u:p@h/db"),
        ("sqlite:///x.db", "sqlite:///x.db"),
    ],
)
def test_database_urls_are_normalised_for_the_driver(raw, expected):
    assert normalize_database_url(raw) == expected


def test_a_missing_database_url_is_a_configuration_error_not_a_connection_attempt(set_settings):
    set_settings(database_url="")
    with pytest.raises(ConfigError, match="DATABASE_URL"):
        get_engine.__wrapped__()


def test_a_failed_transaction_leaves_nothing_behind():
    incident_id = uuid.uuid4()
    with pytest.raises(RuntimeError, match="boom"), session_scope() as session:
        session.add(Incident(id=incident_id, repo_name="o/r", fingerprint="f" * 64, status="queued"))
        session.flush()
        raise RuntimeError("boom")
    assert repositories.get_incident(incident_id) is None


def test_a_successful_transaction_is_committed():
    incident_id = uuid.uuid4()
    with session_scope() as session:
        session.add(Incident(id=incident_id, repo_name="o/r", fingerprint="f" * 64, status="queued"))
    assert repositories.get_incident(incident_id)["status"] == "queued"
