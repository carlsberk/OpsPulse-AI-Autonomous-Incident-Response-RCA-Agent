"""Process lifecycle: worker loop resilience, startup with an unreachable database, no leaked error messages."""

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.exc import OperationalError

import src.main as main_module
from src.main import app
from src.services import incident_service
from src.services.incident_service import DatabaseUnavailableError
from src.worker import Worker
from tests.unit.conftest import REPORTER_HEADERS


def test_worker_loop_survives_database_outages_and_unexpected_errors(set_settings):
    set_settings(worker_poll_seconds=0.001)
    worker = Worker()
    outcomes = [DatabaseUnavailableError("down"), RuntimeError("bug"), True, False]
    calls = []

    def fake_run_once():
        calls.append(1)
        if len(calls) == len(outcomes):
            worker._stop.set()
        outcome = outcomes[len(calls) - 1]
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    worker.run_once = fake_run_once  # type: ignore[method-assign]
    worker.run_forever()
    assert len(calls) == 4  # neither the outage nor the bug ended the loop


def test_worker_stop_returns_even_when_it_was_never_started():
    Worker().stop(timeout=0)


def test_startup_tolerates_an_unreachable_database_and_starts_the_embedded_worker(monkeypatch, set_settings):
    events = []

    class FakeWorker:
        def start_in_thread(self):
            events.append("start")

        def stop(self):
            events.append("stop")

    def unreachable():
        raise OperationalError("select 1", {}, Exception("connection refused"))

    monkeypatch.setattr(main_module, "ping_database", unreachable)
    monkeypatch.setattr(main_module, "Worker", FakeWorker)
    set_settings(embedded_worker=True)
    with TestClient(app) as client:
        assert client.get("/healthz").status_code == 200
        assert events == ["start"]
    assert events == ["start", "stop"]


def test_unexpected_errors_return_a_generic_500_without_leaking_the_message(monkeypatch):
    def boom(_incident_id):
        raise RuntimeError("secret detail ghp_" + "a" * 30)

    monkeypatch.setattr(incident_service, "get_incident_with_approval", boom)
    client = TestClient(app, raise_server_exceptions=False)
    response = client.get("/incidents/00000000-0000-0000-0000-000000000001/guidance", headers=REPORTER_HEADERS)
    assert response.status_code == 500
    assert "secret detail" not in response.text and "ghp_" not in response.text
    assert response.json()["error"]["code"] == "internal_error"


@pytest.mark.parametrize("path", ["/healthz", "/readyz"])
def test_probes_never_require_authentication(path):
    assert TestClient(app).get(path).status_code in (200, 503)
