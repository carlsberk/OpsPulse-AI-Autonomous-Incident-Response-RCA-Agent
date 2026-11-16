"""Webhook failure paths: oversized declared bodies, rate limits, id conflicts and a database outage.

Sentry and Alertmanager retry on 5xx, so each of these must answer with the right status and never with a traceback."""

import copy
import json

import pytest

from src.api import integrations
from src.services.incident_service import DatabaseUnavailableError, IncidentConflictError
from tests.unit.test_alertmanager import BEARER, NOTIFICATION
from tests.unit.test_alertmanager import _post as alertmanager_post
from tests.unit.test_sentry import EVENT_ALERT, SECRET
from tests.unit.test_sentry import _post as sentry_post


def _raise(exc):
    def submit(*args, **kwargs):
        raise exc

    return submit


def test_sentry_rejects_a_declared_oversize_body_before_reading_it(client):
    # The header alone is enough: a lying client cannot make the server buffer the body first.
    resp = client.post(
        "/integrations/sentry",
        content=b"{}",
        headers={"Sentry-Hook-Resource": "event_alert", "Sentry-Hook-Signature": "x", "Content-Length": "999999999"},
    )
    assert resp.status_code == 413


def test_alertmanager_rejects_a_declared_oversize_body(client):
    resp = client.post("/integrations/alertmanager", content=b"{}", headers={**BEARER, "Content-Length": "999999999"})
    assert resp.status_code == 413


def test_sentry_signed_garbage_is_unprocessable_not_a_server_error(client):
    import hashlib
    import hmac

    body = b"this is not json"
    sig = hmac.new(SECRET.encode(), body, hashlib.sha256).hexdigest()
    resp = client.post(
        "/integrations/sentry",
        content=body,
        headers={"Sentry-Hook-Resource": "event_alert", "Sentry-Hook-Signature": sig},
    )
    assert resp.status_code == 422 and "not valid JSON" in resp.json()["error"]["message"]


def test_sentry_is_rate_limited_with_a_retry_after_header(client, set_settings):
    set_settings(rate_limit_per_minute=1)
    first = sentry_post(client, EVENT_ALERT)
    other = copy.deepcopy(EVENT_ALERT)
    other["data"]["event"]["event_id"] = "d" * 32
    limited = sentry_post(client, other)
    assert first.status_code == 202
    assert limited.status_code == 429 and int(limited.headers["Retry-After"]) >= 1


def test_sentry_reports_an_id_conflict_as_409(client, monkeypatch):
    monkeypatch.setattr(integrations.incident_service, "submit_incident", _raise(IncidentConflictError("id reused")))
    resp = sentry_post(client, EVENT_ALERT)
    assert resp.status_code == 409 and "id reused" in resp.json()["error"]["message"]


def test_sentry_reports_a_database_outage_as_503_so_sentry_retries(client, monkeypatch):
    monkeypatch.setattr(integrations.incident_service, "submit_incident", _raise(DatabaseUnavailableError("down")))
    resp = sentry_post(client, EVENT_ALERT)
    assert resp.status_code == 503 and "down" not in resp.text  # no internals in the body


def test_alertmanager_database_outage_is_503_so_the_notification_is_retried(client, monkeypatch):
    monkeypatch.setattr(integrations.incident_service, "submit_incident", _raise(DatabaseUnavailableError("down")))
    assert alertmanager_post(client, NOTIFICATION).status_code == 503


def test_alertmanager_id_conflict_skips_that_alert_and_keeps_the_notification_successful(client, monkeypatch):
    monkeypatch.setattr(integrations.incident_service, "submit_incident", _raise(IncidentConflictError("x")))
    resp = alertmanager_post(client, NOTIFICATION)
    (outcome,) = resp.json()["alerts"]
    assert resp.status_code == 202 and outcome["outcome"] == "skipped"
    assert "different content" in outcome["detail"]


def test_alertmanager_repeat_delivery_is_reported_as_duplicate(client):
    assert alertmanager_post(client, NOTIFICATION).json()["alerts"][0]["outcome"] == "queued"
    assert alertmanager_post(client, NOTIFICATION).json()["alerts"][0]["outcome"] == "duplicate"


@pytest.mark.parametrize("body", [b"not json", json.dumps([1, 2]).encode()])
def test_alertmanager_non_object_bodies_are_422(client, body):
    resp = client.post("/integrations/alertmanager", content=body, headers=BEARER)
    assert resp.status_code == 422
