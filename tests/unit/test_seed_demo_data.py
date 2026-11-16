"""`scripts.seed_demo_data` fills a local database for the console demo. It must keep working as the dataset evolves."""

import importlib
import sys
import uuid

import pytest

from src.db import repositories


@pytest.fixture
def seed(monkeypatch):
    # The module sets demo environment defaults at import time; pre-set them so nothing leaks out of this test.
    for name in ("ENABLE_GITHUB_REMEDIATION", "GITHUB_TOKEN", "REQUIRE_REMEDIATION_APPROVAL"):
        monkeypatch.setenv(name, "x" if name == "GITHUB_TOKEN" else "true")
    monkeypatch.delitem(sys.modules, "scripts.seed_demo_data", raising=False)
    module = importlib.import_module("scripts.seed_demo_data")
    yield module
    sys.modules.pop("scripts.seed_demo_data", None)


def test_every_demo_case_exists_in_the_evaluation_dataset(seed):
    from evals.dataset import load_rca_dataset

    known = {case.id for case in load_rca_dataset().cases}
    assert set(seed.DEMO_CASES) <= known and len(set(seed.DEMO_CASES)) == len(seed.DEMO_CASES)


def test_seeding_creates_one_incident_per_demo_case_submitted_by_the_demo_reporter(seed, capsys):
    assert seed.main() == 0
    rows, _ = repositories.list_incidents(None, None, 100, None)
    assert len(rows) == len(seed.DEMO_CASES)
    out = capsys.readouterr().out
    assert all(case in out for case in seed.DEMO_CASES) and "/console" in out
    submitters = {repositories.get_incident(uuid.UUID(r["incident_id"]))["submitted_by"] for r in rows}
    assert submitters == {"demo-reporter"}


def test_the_seeded_set_gives_a_reviewer_something_to_decide(seed, set_settings):
    # In a real run the module's environment defaults are in place before settings load; here settings already exist.
    set_settings(enable_github_remediation=True, github_token="x", require_remediation_approval=True)
    seed.main()
    statuses = {r["status"] for r in repositories.list_incidents(None, None, 100, None)[0]}
    assert "awaiting_approval" in statuses  # the console demo is pointless without a pending proposal


def test_without_a_database_url_it_refuses_with_one_line(seed, set_settings):
    set_settings(database_url="")
    with pytest.raises(SystemExit, match="DATABASE_URL"):
        seed.main()
