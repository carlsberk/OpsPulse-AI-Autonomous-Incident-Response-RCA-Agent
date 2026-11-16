"""Worker edges the queue tests do not reach: a broken liveness file, a heartbeat that outlives its claim or hits a
database error, the loop's back-off, start/stop, and the process entry point."""

import signal
import threading
import time
import uuid
from datetime import timedelta

from sqlalchemy.exc import OperationalError

from src import worker
from src.services.incident_service import DatabaseUnavailableError


def _wait_for(predicate, seconds=5.0):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.01)
    return False


def test_an_unwritable_liveness_file_is_logged_not_raised(set_settings, tmp_path, caplog):
    set_settings(worker_liveness_file=str(tmp_path / "missing-dir" / "alive"))
    worker.touch_liveness()  # must not raise: liveness is advisory
    assert "could not update the liveness file" in caplog.text


def test_no_liveness_file_configured_means_nothing_to_touch(set_settings):
    set_settings(worker_liveness_file="")
    worker.touch_liveness()


def test_the_heartbeat_stops_when_the_claim_is_no_longer_ours(monkeypatch):
    calls = []
    monkeypatch.setattr(worker.repositories, "renew_lease", lambda *args: calls.append(args) or False)
    beat = worker.LeaseHeartbeat(uuid.uuid4(), "token", timedelta(seconds=30), interval=0.01)
    with beat:
        assert _wait_for(lambda: len(calls) >= 1)
        assert _wait_for(lambda: not beat._thread.is_alive())
    assert len(calls) == 1 and beat.renewals == 0  # it gave up at the first refusal instead of retrying


def test_a_database_error_during_renewal_is_logged_and_the_heartbeat_keeps_going(monkeypatch, caplog):
    outcomes = iter([OperationalError("s", {}, Exception("down")), True, True])

    def renew(*args):
        result = next(outcomes, True)
        if isinstance(result, Exception):
            raise result
        return result

    monkeypatch.setattr(worker.repositories, "renew_lease", renew)
    beat = worker.LeaseHeartbeat(uuid.uuid4(), "token", timedelta(seconds=30), interval=0.01)
    with beat:
        assert _wait_for(lambda: beat.renewals >= 2)
    assert "lease renewal failed: OperationalError" in caplog.text


def test_the_default_renewal_interval_is_a_third_of_the_lease_within_bounds():
    for lease, expected in ((0.5, 1.0), (30, 10.0), (3000, 30.0)):
        beat = worker.LeaseHeartbeat(uuid.uuid4(), "t", timedelta(seconds=lease))
        assert beat.interval == expected


def test_the_loop_backs_off_on_a_database_outage_and_resets_when_it_recovers(monkeypatch, set_settings):
    set_settings(worker_poll_seconds=1.0)
    instance = worker.Worker()
    waits, results = [], iter([DatabaseUnavailableError("down"), DatabaseUnavailableError("down"), False, True])

    def run_once():
        result = next(results)
        if isinstance(result, Exception):
            raise result
        if result is True:
            instance._stop.set()
        return result

    monkeypatch.setattr(instance, "run_once", run_once)
    monkeypatch.setattr(instance._stop, "wait", lambda seconds: waits.append(seconds) or False)
    instance.run_forever()
    assert waits == [
        2.0,
        4.0,
        1.0,
    ]  # doubled twice, back to the poll interval once the database answers; busy means no wait


def test_the_back_off_never_exceeds_the_cap(monkeypatch, set_settings):
    set_settings(worker_poll_seconds=20.0)
    instance = worker.Worker()
    waits, count = [], iter(range(3))

    def run_once():
        if next(count, None) is None:
            instance._stop.set()
            return True
        raise DatabaseUnavailableError("down")

    monkeypatch.setattr(instance, "run_once", run_once)
    monkeypatch.setattr(instance._stop, "wait", lambda seconds: waits.append(seconds) or False)
    instance.run_forever()
    assert waits and max(waits) == worker.MAX_BACKOFF_SECONDS


def test_the_worker_can_be_started_in_a_thread_and_stopped(monkeypatch, set_settings):
    set_settings(worker_poll_seconds=0.01)
    instance = worker.Worker()
    polled = threading.Event()
    monkeypatch.setattr(instance, "run_once", lambda: polled.set() or False)
    instance.start_in_thread()
    assert polled.wait(5)
    instance.stop(timeout=5)
    assert instance._thread is not None and not instance._thread.is_alive()


def test_stopping_a_worker_that_never_started_is_harmless():
    worker.Worker().stop()


def test_the_healthcheck_exit_code_follows_the_liveness_file(monkeypatch, set_settings, tmp_path):
    alive = tmp_path / "alive"
    set_settings(worker_liveness_file=str(alive))
    monkeypatch.setattr(worker.sys, "argv", ["worker", "--healthcheck"])
    assert worker.main() == 1  # no file yet
    alive.touch()
    assert worker.main() == 0


def test_main_installs_signal_handlers_that_stop_the_loop(monkeypatch):
    installed = {}
    monkeypatch.setattr(worker.sys, "argv", ["worker"])
    monkeypatch.setattr(worker.signal, "signal", lambda signum, handler: installed.update({signum: handler}))
    seen = {}

    def run_forever(self):
        installed[signal.SIGTERM](signal.SIGTERM, None)  # what the container runtime sends on stop
        seen["stopped"] = self._stop.is_set()

    monkeypatch.setattr(worker.Worker, "run_forever", run_forever)
    assert worker.main() == 0
    assert set(installed) == {signal.SIGINT, signal.SIGTERM} and seen["stopped"] is True
