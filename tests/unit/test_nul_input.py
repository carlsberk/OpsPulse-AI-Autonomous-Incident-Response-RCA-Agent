"""NUL (U+0000) in input: SQLite stores it, PostgreSQL rejects it, so it is removed before anything is persisted."""

import uuid

from src.db import repositories
from tests.unit.conftest import API_HEADERS
from tests.unit.test_alertmanager import BEARER, NOTIFICATION
from tests.unit.test_sentry import EVENT_ALERT
from tests.unit.test_sentry import _post as sentry_post

RAW = b'{"repo_name":"o/r","error_message":"Type\\u0000Error: x","stack_trace":"line\\u0000 1"}'


def test_nul_bytes_in_a_reported_error_are_not_stored(client):
    resp = client.post("/webhook/incident", content=RAW, headers={**API_HEADERS, "Content-Type": "application/json"})
    assert resp.status_code == 202
    row = repositories.get_incident(uuid.UUID(resp.json()["incident_id"]))
    assert row["error_message"] == "TypeError: x" and "\x00" not in str(row)


def test_the_same_error_with_and_without_nul_is_one_failure():
    from src.agent.parsing import compute_fingerprint
    from src.services.incident_service import _without_nul

    a, b = "Type\x00Error: x", "TypeError: x"
    assert compute_fingerprint("o/r", _without_nul(a), "") == compute_fingerprint("o/r", b, "")


def test_nul_in_a_sentry_event_is_removed_too(client):
    event = EVENT_ALERT.copy()
    event["data"] = {**EVENT_ALERT["data"], "event": {**EVENT_ALERT["data"]["event"], "title": "Bad\x00Thing"}}
    resp = sentry_post(client, event)
    assert resp.status_code == 202
    row = repositories.get_incident(uuid.UUID(resp.json()["incident_id"]))
    assert "\x00" not in row["error_message"]


def test_nul_in_an_alertmanager_alert_is_removed_too(client):
    notification = {**NOTIFICATION, "alerts": [dict(NOTIFICATION["alerts"][0])]}
    notification["alerts"][0]["annotations"] = {**notification["alerts"][0]["annotations"], "summary": "Disk\x00 full"}
    resp = client.post("/integrations/alertmanager", json=notification, headers=BEARER)
    assert resp.status_code == 202
    rows, _ = repositories.list_incidents(None, None, 10, None)
    stored = repositories.get_incident(uuid.UUID(rows[0]["incident_id"]))
    assert "\x00" not in stored["error_message"]


def test_a_lone_surrogate_in_a_sentry_event_is_replaced_not_a_server_error(client):
    import copy

    event = copy.deepcopy(EVENT_ALERT)
    event["data"]["event"]["exception"]["values"][0]["value"] = "bad \ud800 char"
    resp = sentry_post(client, event)  # json.dumps escapes it as \ud800 on the wire, and the signature covers that body
    assert resp.status_code == 202
    row = repositories.get_incident(uuid.UUID(resp.json()["incident_id"]))
    assert "bad ? char" in row["error_message"]


def test_a_lone_surrogate_in_an_alertmanager_alert_is_replaced_not_a_server_error(client):
    import json

    notification = json.loads(json.dumps(NOTIFICATION))
    notification["alerts"][0]["annotations"]["summary"] = "bad \ud800 char"
    resp = client.post("/integrations/alertmanager", content=json.dumps(notification).encode(), headers=BEARER)
    assert resp.status_code == 202 and resp.json()["alerts"][0]["outcome"] == "queued"


def test_storable_text_keeps_real_unicode_and_only_replaces_what_cannot_be_encoded():
    from src.services.incident_service import _storable

    assert _storable("ünïcödé 日本語 😀") == "ünïcödé 日本語 😀"
    assert _storable("a\x00b\ud800c") == "ab?c"


def test_an_unsigned_body_that_cannot_be_re_encoded_is_a_401_not_a_server_error(client):
    # Before the signature check passes, an anonymous client controls the body: this used to raise UnicodeEncodeError.
    body = b'{"data": {"event": {"title": "bad \\ud800"}}}'
    resp = client.post(
        "/integrations/sentry",
        content=body,
        headers={"Sentry-Hook-Resource": "event_alert", "Sentry-Hook-Signature": "0" * 64},
    )
    assert resp.status_code == 401


def test_absurdly_nested_json_is_a_401_not_a_server_error(client):
    resp = client.post(
        "/integrations/sentry",
        content=b"[" * 200_000 + b"]" * 200_000,
        headers={"Sentry-Hook-Resource": "event_alert", "Sentry-Hook-Signature": "0" * 64},
    )
    assert resp.status_code == 401


def test_a_non_ascii_signature_header_is_a_401_not_a_server_error(client):
    # Found by a property test: hmac.compare_digest raised TypeError for a str containing a non-ASCII character.
    resp = client.post(
        "/integrations/sentry",
        content=b"{}",
        headers=[(b"sentry-hook-resource", b"event_alert"), (b"sentry-hook-signature", b"\xe9\xff caf\xe9")],
    )
    assert resp.status_code == 401
