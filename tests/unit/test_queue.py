"""Durable queue: compare-and-set claims, lease expiry, bounded re-queueing, worker resilience."""

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import update
from sqlalchemy.exc import OperationalError

from src.db import repositories
from src.db.client import session_scope
from src.db.models import Incident
from src.services import incident_service
from src.services.incident_service import DatabaseUnavailableError
from src.worker import Worker
from tests.unit.conftest import API_HEADERS
from tests.unit.factories import incident_payload, make_analysis

LEASE = timedelta(minutes=30)


def _queued():
    incident_id = uuid.uuid4()
    repositories.create_incident(incident_id, "o/r", "f" * 64, "TypeError: x", "trace")
    return incident_id


def _expire(incident_id, lease_expires_at=None, updated_at=None):
    values = {"lease_expires_at": lease_expires_at or datetime.now(UTC) - timedelta(minutes=1)}
    if updated_at:
        values["updated_at"] = updated_at
    with session_scope() as session:
        session.execute(update(Incident).where(Incident.id == incident_id).values(**values))


def test_async_submission_is_queued_then_processed_by_a_worker(client, fake_llm, source_fetch):
    fake_llm([make_analysis()])
    resp = client.post("/webhook/incident", json=incident_payload(), headers=API_HEADERS)
    assert resp.status_code == 202 and resp.json()["status"] == "queued"
    worker = Worker()
    assert worker.run_once() is True
    assert worker.run_once() is False  # queue drained
    body = client.get(f"/incidents/{resp.json()['incident_id']}", headers=API_HEADERS).json()
    assert body["status"] == "analysis_ready" and body["job_attempts"] == 1


def test_a_claim_can_only_be_won_once():
    incident_id = _queued()
    assert repositories.claim_incident(incident_id, LEASE)
    assert repositories.claim_incident(incident_id, LEASE) is None
    assert repositories.claim_next(LEASE) is None


def test_expired_claims_are_requeued_then_failed_after_max_attempts(set_settings):
    set_settings(max_job_attempts=2)
    worker = Worker()
    incident_id = _queued()
    for expected_attempt in (1, 2):
        assert repositories.claim_incident(incident_id, LEASE)
        _expire(incident_id)
        requeued, failed = worker.recover_expired()
        row = repositories.get_incident(incident_id)
        assert row["job_attempts"] == expected_attempt
        if expected_attempt == 1:
            assert (requeued, failed) == (1, 0) and row["status"] == "queued"
        else:
            assert (requeued, failed) == (0, 1) and row["status"] == "failed"
            assert row["error_category"] == "interrupted"


def test_live_claims_are_left_alone():
    incident_id = _queued()
    repositories.claim_incident(incident_id, LEASE)
    assert Worker().recover_expired() == (0, 0)
    assert repositories.get_incident(incident_id)["status"] == "processing"


def test_rows_from_before_the_queue_migration_are_recovered():
    incident_id = _queued()
    with session_scope() as session:  # `processing` without a lease, as left by the old in-process runner
        session.execute(
            update(Incident)
            .where(Incident.id == incident_id)
            .values(status="processing", lease_expires_at=None, updated_at=datetime.now(UTC) - timedelta(hours=2))
        )
    assert Worker().recover_expired() == (1, 0)


def test_worker_loop_survives_an_unexpected_error(monkeypatch):
    incident_id = _queued()
    worker = Worker()

    def boom(*_args, **_kwargs):
        worker._stop.set()  # end the loop after this iteration
        raise RuntimeError("bug in the pipeline")

    monkeypatch.setattr(incident_service, "run_incident_pipeline", boom)
    worker.run_forever()  # must return normally instead of raising
    row = repositories.get_incident(incident_id)
    assert row["status"] == "processing"  # lease still held; it will be re-queued when it expires


def test_database_outage_is_reported_to_the_loop(monkeypatch):
    def down(*_args, **_kwargs):
        raise OperationalError("SELECT", {}, Exception("connection refused"))

    monkeypatch.setattr(repositories, "requeue_expired", down)
    with pytest.raises(DatabaseUnavailableError):
        Worker().run_once()


def test_wait_mode_does_not_run_an_incident_a_worker_already_claimed(client, monkeypatch, fake_llm, source_fetch):
    llm = fake_llm([make_analysis()])
    monkeypatch.setattr(incident_service, "claim_for_inline_run", lambda incident_id: False)
    resp = client.post("/webhook/incident?wait=true", json=incident_payload(), headers=API_HEADERS)
    assert resp.status_code == 202 and resp.json()["status"] == "processing" and llm.calls == 0


def test_heartbeat_renews_the_lease_while_work_runs_and_stops_after():
    import time

    from src.worker import LeaseHeartbeat

    incident_id = _queued()
    token = repositories.claim_incident(incident_id, timedelta(seconds=1))
    with LeaseHeartbeat(incident_id, token, timedelta(minutes=5), interval=0.05) as heartbeat:
        time.sleep(0.3)
    assert heartbeat.renewals >= 2
    with session_scope() as session:
        lease = session.get(Incident, incident_id).lease_expires_at
    lease = lease if lease.tzinfo else lease.replace(tzinfo=UTC)
    assert lease > datetime.now(UTC) + timedelta(minutes=4)  # extended far beyond the original 1 second
    assert Worker().recover_expired() == (0, 0)


def test_heartbeat_stops_renewing_once_the_incident_is_finished():
    incident_id = _queued()
    token = repositories.claim_incident(incident_id, LEASE)
    repositories.update_incident(incident_id, status="analysis_ready")
    assert repositories.renew_lease(incident_id, token, LEASE) is False


def test_only_the_current_claim_can_renew_or_write():
    from src.db.repositories import ClaimLostError

    incident_id = _queued()
    stale = repositories.claim_incident(incident_id, LEASE)
    _expire(incident_id)
    Worker().recover_expired()  # back to the queue
    fresh = repositories.claim_incident(incident_id, LEASE)
    assert fresh and fresh != stale
    assert repositories.renew_lease(incident_id, stale, LEASE) is False
    assert repositories.renew_lease(incident_id, fresh, LEASE) is True
    with pytest.raises(ClaimLostError):
        repositories.update_incident(incident_id, expected_claim_token=stale, status="analysis_ready")
    repositories.update_incident(incident_id, expected_claim_token=fresh, status="analysis_ready")


def test_a_worker_that_lost_its_claim_discards_its_result_and_proposes_nothing(
    client, fake_llm, source_fetch, set_settings
):
    from src.db.repositories import get_pending_approval

    set_settings(enable_github_remediation=True, github_token="test-token", require_remediation_approval=True)
    fake_llm([make_analysis()])
    incident_id = _queued()
    stale = repositories.claim_incident(incident_id, LEASE)
    _expire(incident_id)
    Worker().recover_expired()
    repositories.claim_incident(incident_id, LEASE)  # another worker now owns it
    result = incident_service.run_incident_pipeline(incident_id, "o/r", "TypeError: x", "trace", claim_token=stale)
    assert result["status"] == "processing" and get_pending_approval(incident_id) is None


def test_worker_liveness_file_is_touched_and_checked(tmp_path, set_settings):
    import os
    import time

    from src.worker import is_alive, touch_liveness

    marker = tmp_path / "alive"
    set_settings(worker_liveness_file=str(marker))
    assert is_alive() is False  # never touched
    touch_liveness()
    assert is_alive() is True
    old = time.time() - 600
    os.utime(marker, (old, old))
    assert is_alive() is False  # stale: the healthcheck would fail
    Worker().run_once()
    touch_liveness()
    assert is_alive() is True


def test_competing_workers_win_every_incident_exactly_once():
    """Several threads drain a queue at the same time: no incident is claimed twice and none is skipped."""
    import threading

    ids = {_queued() for _ in range(12)}
    claimed: list = []
    errors: list = []
    guard = threading.Lock()

    def drain():
        for _ in range(60):  # SQLite serialises writers; a locked database is retried, never a lost or double claim
            try:
                job = repositories.claim_next(LEASE)
            except OperationalError:
                continue
            if job is None:
                if len(claimed) >= len(ids):
                    return
                continue
            with guard:
                claimed.append(job["incident_id"])

    threads = [threading.Thread(target=lambda: _guarded(drain, errors)) for _ in range(4)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=60)
    assert not errors, errors
    assert sorted(map(str, claimed)) == sorted(map(str, ids)), "an incident was claimed twice or never"


def _guarded(fn, errors):
    try:
        fn()
    except Exception as exc:  # the test reports whatever escaped a worker thread
        errors.append(repr(exc))
