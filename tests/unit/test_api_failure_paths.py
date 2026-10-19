"""API failure paths: a database outage is a 503 everywhere, conflicts are 409, unknown ids are 404, and the
Alertmanager receiver degrades per alert instead of failing the whole notification."""

import copy
import uuid

import pytest

from src.services import ask_service, incident_service
from src.services.incident_service import DatabaseUnavailableError, IncidentConflictError, IncidentNotFoundError
from tests.unit.conftest import API_HEADERS, REPORTER_HEADERS, REVIEWER_HEADERS
from tests.unit.factories import incident_payload
from tests.unit.test_alertmanager import BEARER, NOTIFICATION

ID = "00000000-0000-0000-0000-000000000042"


def _down(*_args, **_kwargs):
    raise DatabaseUnavailableError("database unavailable")


def _not_found(*_args, **_kwargs):
    raise IncidentNotFoundError("incident not found")


def _conflict(*_args, **_kwargs):
    raise IncidentConflictError("cannot do that now")


SVC, ASK = incident_service, ask_service
GET_INCIDENT = "get_incident_with_approval"
DECISION = {"approval_id": ID, "patch_sha256": "0" * 64, "decision": "reject"}

# (method, path, headers, json, function to break)
ENDPOINTS = [
    ("get", "/incidents", REPORTER_HEADERS, None, (incident_service, "list_incidents")),
    ("post", "/webhook/incident", API_HEADERS, incident_payload(), (incident_service, "submit_incident")),
    ("get", f"/incidents/{ID}", REPORTER_HEADERS, None, (SVC, GET_INCIDENT)),
    ("post", f"/incidents/{ID}/remediation/decision", REVIEWER_HEADERS, DECISION, (SVC, "decide_remediation")),
    ("post", f"/incidents/{ID}/retry", REVIEWER_HEADERS, None, (incident_service, "retry_incident")),
    ("post", f"/incidents/{ID}/ask", REPORTER_HEADERS, {"question": "why?"}, (ask_service, "ask")),
    ("get", f"/incidents/{ID}/trace", REPORTER_HEADERS, None, (SVC, GET_INCIDENT)),
    ("get", f"/incidents/{ID}/evidence-graph", REPORTER_HEADERS, None, (SVC, GET_INCIDENT)),
    ("get", f"/incidents/{ID}/audit", REPORTER_HEADERS, None, (incident_service, "get_audit")),
    ("get", f"/incidents/{ID}/guidance", REPORTER_HEADERS, None, (SVC, GET_INCIDENT)),
]  # fmt: skip


@pytest.mark.parametrize("method,path,headers,body,target", ENDPOINTS, ids=[f"{e[0]} {e[1][:34]}" for e in ENDPOINTS])
def test_a_database_outage_is_a_503_not_a_crash(client, monkeypatch, method, path, headers, body, target):
    monkeypatch.setattr(target[0], target[1], _down)
    response = getattr(client, method)(path, headers=headers, **({"json": body} if body is not None else {}))
    assert response.status_code == 503, response.text
    assert "database unavailable" in response.text


@pytest.mark.parametrize(
    "path,headers,body,target",
    [
        (f"/incidents/{ID}/retry", REVIEWER_HEADERS, None, (incident_service, "retry_incident")),
        (f"/incidents/{ID}/remediation/decision", REVIEWER_HEADERS, DECISION, (SVC, "decide_remediation")),
        (f"/incidents/{ID}/ask", REPORTER_HEADERS, {"question": "why?"}, (ask_service, "ask")),
    ],
)
def test_unknown_incident_is_a_404(client, monkeypatch, path, headers, body, target):
    monkeypatch.setattr(target[0], target[1], _not_found)
    response = client.post(path, headers=headers, **({"json": body} if body is not None else {}))
    assert response.status_code == 404


def test_retry_of_an_incident_that_cannot_be_retried_is_a_409(client, monkeypatch):
    monkeypatch.setattr(incident_service, "retry_incident", _conflict)
    assert client.post(f"/incidents/{ID}/retry", headers=REVIEWER_HEADERS).status_code == 409


def test_conflicting_submission_is_a_409(client, monkeypatch):
    monkeypatch.setattr(incident_service, "submit_incident", _conflict)
    assert client.post("/webhook/incident", json=incident_payload(), headers=API_HEADERS).status_code == 409


def test_a_malformed_pagination_cursor_is_a_400(client):
    assert client.get("/incidents?cursor=not-a-cursor", headers=REPORTER_HEADERS).status_code in (400, 422)


def test_a_non_uuid_incident_id_is_a_422(client):
    assert client.get("/incidents/not-a-uuid", headers=REPORTER_HEADERS).status_code == 422


# ------------------------------------------------------------------ Alertmanager receiver


def _post(client, payload, headers=BEARER, **kwargs):
    return client.post("/integrations/alertmanager", json=payload, headers=headers, **kwargs)


def test_an_oversized_notification_is_refused_before_it_is_read(client, monkeypatch):
    from src.integrations import alertmanager

    monkeypatch.setattr(alertmanager, "MAX_BODY_BYTES", 50)
    assert _post(client, NOTIFICATION).status_code == 413


def test_a_body_that_is_not_json_is_a_422(client):
    response = client.post(
        "/integrations/alertmanager", content=b"{not json", headers={**BEARER, "Content-Type": "application/json"}
    )
    assert response.status_code == 422


def test_alerts_without_a_usable_repository_are_skipped_individually(client, set_settings):
    payload = copy.deepcopy(NOTIFICATION)
    payload["alerts"] = [payload["alerts"][0], copy.deepcopy(payload["alerts"][0]), copy.deepcopy(payload["alerts"][0])]
    payload["alerts"][1]["labels"].pop("repository")
    payload["alerts"][1]["fingerprint"] = "bbbb"
    payload["alerts"][2]["labels"]["repository"] = "someone/else"
    payload["alerts"][2]["fingerprint"] = "cccc"
    outcomes = _post(client, payload).json()["alerts"]
    assert [o["outcome"] for o in outcomes] == ["queued", "skipped", "skipped"]
    assert "missing" in outcomes[1]["detail"] and "not allowed" in outcomes[2]["detail"]


def test_a_conflicting_alert_is_skipped_but_the_rest_are_processed(client, monkeypatch):
    real = incident_service.submit_incident
    seen = []

    def flaky(incident_id, *args, **kwargs):
        seen.append(incident_id)
        if len(seen) == 1:
            raise IncidentConflictError("id reused")
        return real(incident_id, *args, **kwargs)

    payload = copy.deepcopy(NOTIFICATION)
    second = copy.deepcopy(payload["alerts"][0])
    second["fingerprint"] = "dddd"
    payload["alerts"] = [payload["alerts"][0], second]
    monkeypatch.setattr(incident_service, "submit_incident", flaky)
    outcomes = _post(client, payload).json()["alerts"]
    assert [o["outcome"] for o in outcomes] == ["skipped", "queued"]


def test_a_database_outage_makes_alertmanager_retry_the_whole_notification(client, monkeypatch):
    monkeypatch.setattr(incident_service, "submit_incident", _down)
    assert _post(client, NOTIFICATION).status_code == 503


def test_unknown_ids_in_paths_do_not_exist(client):
    assert client.get(f"/incidents/{uuid.uuid4()}", headers=REPORTER_HEADERS).status_code == 404
