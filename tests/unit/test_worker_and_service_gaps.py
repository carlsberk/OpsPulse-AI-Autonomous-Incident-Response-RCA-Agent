"""Worker process lifecycle and the service layer's database-outage handling (previously untested branches)."""

import signal
import sys
import time
import uuid
from datetime import timedelta

import pytest
from sqlalchemy.exc import OperationalError

from src import worker as worker_module
from src.db import repositories
from src.errors import TRANSIENT_CATEGORIES
from src.services import incident_service, remediation_service
from src.services.incident_service import DatabaseUnavailableError
from src.worker import LeaseHeartbeat, Worker, is_alive, touch_liveness
from tests.unit.conftest import API_HEADERS
from tests.unit.factories import incident_payload, make_analysis


def _db_down(*_args, **_kwargs):
    raise OperationalError("SELECT", {}, Exception("connection refused"))


def _wait_for(predicate, timeout=3.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.01)
    return predicate()


# ------------------------------------------------------------------ worker: liveness


def test_an_unwritable_liveness_file_never_crashes_the_worker(tmp_path, set_settings):
    set_settings(worker_liveness_file=str(tmp_path / "missing-dir" / "alive"))
    touch_liveness()  # logs a warning, must not raise
    assert is_alive() is False


def test_no_liveness_file_configured_is_a_no_op(set_settings):
    set_settings(worker_liveness_file="")
    touch_liveness()


# ------------------------------------------------------------------ worker: lease heartbeat


def test_heartbeat_stops_when_the_claim_is_no_longer_ours(monkeypatch):
    calls = []

    def lost(*_args):
        calls.append(1)
        return False

    monkeypatch.setattr(repositories, "renew_lease", lost)
    with LeaseHeartbeat(uuid.uuid4(), "token", timedelta(minutes=5), interval=0.01) as heartbeat:
        assert _wait_for(lambda: not heartbeat._thread.is_alive())
    assert calls == [1] and heartbeat.renewals == 0  # asked once, then gave up


def test_heartbeat_survives_a_database_blip_and_keeps_renewing(monkeypatch):
    outcomes = iter([OperationalError("UPDATE", {}, Exception("blip")), True, True, True])

    def flaky(*_args):
        outcome = next(outcomes, True)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    monkeypatch.setattr(repositories, "renew_lease", flaky)
    with LeaseHeartbeat(uuid.uuid4(), "token", timedelta(minutes=5), interval=0.01) as heartbeat:
        assert _wait_for(lambda: heartbeat.renewals >= 2)
    assert heartbeat.renewals >= 2


def test_heartbeat_interval_is_a_third_of_the_lease_within_bounds():
    assert LeaseHeartbeat(uuid.uuid4(), "t", timedelta(seconds=30)).interval == 10
    assert LeaseHeartbeat(uuid.uuid4(), "t", timedelta(seconds=1)).interval == 1.0  # floor
    assert LeaseHeartbeat(uuid.uuid4(), "t", timedelta(hours=1)).interval == 30.0  # cap keeps liveness fresh


# ------------------------------------------------------------------ worker: thread and process entry point


def test_embedded_worker_thread_starts_and_stops_cleanly(monkeypatch, set_settings):
    set_settings(worker_poll_seconds=0.01)
    iterations = []

    def idle(self):
        iterations.append(1)
        return False

    monkeypatch.setattr(Worker, "run_once", idle)
    worker = Worker()
    worker.start_in_thread()
    assert _wait_for(lambda: len(iterations) >= 2)
    worker.stop(timeout=3)
    assert worker._thread is not None and not worker._thread.is_alive()


def test_healthcheck_flag_reports_liveness_through_the_exit_code(monkeypatch, tmp_path, set_settings):
    marker = tmp_path / "alive"
    set_settings(worker_liveness_file=str(marker))
    monkeypatch.setattr(sys, "argv", ["worker", "--healthcheck"])
    assert worker_module.main() == 1  # never touched
    touch_liveness()
    assert worker_module.main() == 0


def test_main_runs_the_loop_and_signals_stop_the_worker(monkeypatch):
    events = {}

    class FakeWorker:
        def run_forever(self):
            events["ran"] = True

        def stop(self, timeout=5.0):
            events["stopped_with"] = timeout

    handlers = {}
    monkeypatch.setattr(sys, "argv", ["worker"])
    monkeypatch.setattr(worker_module, "Worker", FakeWorker)
    monkeypatch.setattr("src.logging_config.configure_logging", lambda level: events.setdefault("logging", level))
    monkeypatch.setattr(signal, "signal", lambda signum, handler: handlers.__setitem__(signum, handler))

    assert worker_module.main() == 0
    assert events["ran"] and "logging" in events
    assert set(handlers) == {signal.SIGINT, signal.SIGTERM}
    handlers[signal.SIGTERM](signal.SIGTERM, None)  # graceful shutdown: do not wait for the running analysis
    assert events["stopped_with"] == 0


def test_run_forever_backs_off_exponentially_during_an_outage_and_resets_after(monkeypatch, set_settings):
    set_settings(worker_poll_seconds=1.0)
    worker = Worker()
    waits = []
    results = iter([DatabaseUnavailableError("down"), DatabaseUnavailableError("down"), False])

    def run_once():
        outcome = next(results)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    def record_wait(seconds):
        waits.append(seconds)
        if len(waits) == 3:
            worker._stop.set()
        return worker._stop.is_set()

    monkeypatch.setattr(worker, "run_once", run_once)
    monkeypatch.setattr(worker._stop, "wait", record_wait)
    worker.run_forever()
    assert waits == [2.0, 4.0, 1.0]  # doubled twice, then back to the poll interval after a good iteration


# ------------------------------------------------------------------ service: build_graph cache


def test_the_application_graph_is_built_once_and_cached(monkeypatch):
    built = []
    sentinel = object()
    monkeypatch.setattr(incident_service, "build_graph", lambda *args: built.append(args) or sentinel)
    incident_service.get_graph.cache_clear()
    try:
        assert incident_service.get_graph() is sentinel
        assert incident_service.get_graph() is sentinel
        assert len(built) == 1
    finally:
        incident_service.get_graph.cache_clear()


# ------------------------------------------------------------------ service: database outages become one error type

_ID = uuid.uuid4()


@pytest.mark.parametrize(
    ("repo_function", "call"),
    [
        ("list_incidents", lambda: incident_service.list_incidents(None, None, 10, None)),
        ("get_incident", lambda: incident_service.get_incident_with_approval(_ID)),
        ("get_incident", lambda: incident_service.get_audit(_ID)),
        ("claim_incident", lambda: incident_service.claim_for_inline_run(_ID)),
        ("get_incident", lambda: incident_service.retry_incident(_ID, "alice")),
        ("get_incident", lambda: incident_service.decide_remediation(_ID, uuid.uuid4(), "0" * 64, True, "bob", None)),
        ("get_incident", lambda: incident_service._defer_transient(_ID, "token", next(iter(TRANSIENT_CATEGORIES)))),
    ],
    ids=["list", "get", "audit", "claim", "retry", "decide", "defer"],
)
def test_a_database_outage_is_reported_as_database_unavailable(monkeypatch, repo_function, call):
    monkeypatch.setattr(repositories, repo_function, _db_down)
    with pytest.raises(DatabaseUnavailableError, match="database unavailable"):
        call()


def test_a_vanished_incident_row_is_an_error_not_a_none(monkeypatch):
    monkeypatch.setattr(incident_service, "get_incident_with_approval", lambda incident_id: None)
    with pytest.raises(DatabaseUnavailableError, match="disappeared"):
        incident_service._load(_ID)


def test_a_database_outage_during_remediation_opens_no_pr_and_surfaces_as_unavailable(
    client, fake_llm, source_fetch, set_settings, monkeypatch
):
    set_settings(enable_github_remediation=True, github_token="test-token", require_remediation_approval=False)
    fake_llm([make_analysis()])
    monkeypatch.setattr(remediation_service, "remediate", _db_down)
    payload = incident_payload()
    resp = client.post("/webhook/incident", json=payload, headers=API_HEADERS)  # queued exactly as the API does
    assert resp.status_code == 202
    incident_id = uuid.UUID(resp.json()["incident_id"])
    with pytest.raises(DatabaseUnavailableError):
        incident_service.run_incident_pipeline(
            incident_id, payload["repo_name"], payload["error_message"], payload["stack_trace"]
        )
    assert repositories.get_incident(incident_id)["pr_url"] is None  # nothing was opened


# ------------------------------------------------------------------ service: the model's optional regression test


@pytest.mark.parametrize(
    "reproduction_test",
    [None, "not-a-dict", {}, {"path": "tests/test_x.py"}, {"content": "x"}, {"path": 1, "content": "x"}],
)
def test_a_malformed_reproduction_test_is_ignored(reproduction_test):
    final = {"root_cause_analysis": {"reproduction_test": reproduction_test}, "error_message": "TypeError: x"}
    assert incident_service._reproduction_spec(final) is None


def test_a_wellformed_reproduction_test_is_handed_to_the_sandbox_with_the_expected_error():
    final = {
        "root_cause_analysis": {"reproduction_test": {"path": "tests/test_repro.py", "content": "def test_x(): ..."}},
        "error_message": "httpx.PoolTimeout: pool exhausted",
    }
    spec = incident_service._reproduction_spec(final)
    assert spec is not None
    assert (spec.path, spec.content, spec.expected_error) == (
        "tests/test_repro.py",
        "def test_x(): ...",
        "PoolTimeout",
    )


def test_missing_analysis_means_no_reproduction_test():
    assert incident_service._reproduction_spec({"root_cause_analysis": None, "error_message": "x"}) is None
