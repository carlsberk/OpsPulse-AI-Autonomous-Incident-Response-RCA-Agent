"""No request, however malformed, may produce a server error (5xx).

Clients are untrusted: every endpoint must answer bad input with a 4xx. A 5xx here is always a bug (an unhandled
exception), so this drives each public route with generated bodies, paths and query strings.
"""

import json
import os
import uuid

from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from tests.unit.conftest import API_HEADERS, REPORTER_HEADERS
from tests.unit.factories import incident_payload

_EXPLORE = os.environ.get("HYPOTHESIS_EXPLORE") == "1"
FUZZ = settings(
    max_examples=int(os.environ.get("HYPOTHESIS_EXAMPLES", "60")),
    derandomize=not _EXPLORE,
    deadline=None,
    suppress_health_check=[HealthCheck.too_slow, HealthCheck.function_scoped_fixture],
)

LEAF = (
    st.none()
    | st.booleans()
    | st.integers(-(10**6), 10**6)
    | st.floats(allow_nan=False, allow_infinity=False)
    | st.text(max_size=40)
)
JSON_VALUE = st.recursive(
    LEAF,
    lambda inner: st.lists(inner, max_size=4) | st.dictionaries(st.text(max_size=10), inner, max_size=5),
    max_leaves=20,
)
WORDS = st.text(alphabet="abcdefghijklmnopqrstuvwxyz0123456789-_/. {}<>\"'`\\%", max_size=40)


def _no_server_error(response):
    assert response.status_code < 500, f"{response.request.method} {response.request.url}: {response.status_code}"


@FUZZ
@given(body=JSON_VALUE)
def test_submit_never_errors(client, body):
    _no_server_error(client.post("/webhook/incident", json=body, headers=API_HEADERS))


@FUZZ
@given(
    repo=st.sampled_from(["o/r", "o/r", "x/y", "O/R", ""]) | WORDS,
    error=WORDS,
    trace=WORDS | st.just(incident_payload()["stack_trace"]),
    incident_id=st.none() | st.uuids().map(str) | WORDS,
)
def test_submit_with_plausible_fields_never_errors(client, repo, error, trace, incident_id):
    body = {"repo_name": repo, "error_message": error, "stack_trace": trace, "incident_id": incident_id}
    _no_server_error(client.post("/webhook/incident", json=body, headers=API_HEADERS))


@FUZZ
@given(status=WORDS, repo=WORDS, limit=WORDS, cursor=WORDS)
def test_list_never_errors(client, status, repo, limit, cursor):
    params = {"status": status, "repo_name": repo, "limit": limit, "cursor": cursor}
    _no_server_error(client.get("/incidents", params=params, headers=API_HEADERS))


@FUZZ
@given(path_id=WORDS | st.uuids().map(str), body=JSON_VALUE)
def test_incident_routes_never_error_for_any_id_and_body(client, path_id, body):
    safe = path_id.replace("/", "_").replace("?", "_").replace("#", "_").replace("\\", "_") or "x"
    for method, suffix in (
        ("GET", ""),
        ("GET", "/guidance"),
        ("POST", "/retry"),
        ("POST", "/ask"),
        ("POST", "/remediation/decision"),
    ):
        response = client.request(method, f"/incidents/{safe}{suffix}", json=body if method == "POST" else None,
                                  headers=API_HEADERS)  # fmt: skip
        _no_server_error(response)


@FUZZ
@given(question=WORDS, history=JSON_VALUE)
def test_ask_never_errors_on_a_real_incident(client, fake_llm, source_fetch, question, history):
    from tests.unit.factories import make_analysis

    fake_llm([make_analysis()])
    created = client.post("/webhook/incident?wait=true", json=incident_payload(incident_id=str(uuid.uuid4())),
                          headers=API_HEADERS)  # fmt: skip
    assert created.status_code == 200
    fake_llm([{"answer": "ok", "answerable": True, "cited_sections": ["summary"]}] * 3)
    body = {"question": question, "history": history}
    _no_server_error(
        client.post(f"/incidents/{created.json()['incident_id']}/ask", json=body, headers=REPORTER_HEADERS)
    )


@FUZZ
@given(body=JSON_VALUE, raw=st.binary(max_size=200))
def test_integrations_never_error(client, body, raw):
    _no_server_error(
        client.post("/integrations/alertmanager", json=body, headers={"Authorization": "Bearer reporter-key"})
    )
    _no_server_error(
        client.post("/integrations/alertmanager", content=raw, headers={"Authorization": "Bearer reporter-key"})
    )
    signed = {"Sentry-Hook-Resource": "event_alert", "Sentry-Hook-Signature": "0" * 64}
    _no_server_error(client.post("/integrations/sentry", content=json.dumps(body).encode(), headers=signed))
    _no_server_error(client.post("/integrations/sentry", content=raw, headers=signed))


@FUZZ
@given(path=WORDS, headers=st.dictionaries(st.sampled_from(["x-api-key", "authorization", "content-type"]), WORDS))
def test_unknown_paths_and_odd_headers_never_error(client, path, headers):
    safe = path.replace("?", "_").replace("#", "_").replace("\\", "_").lstrip("/")
    for target in (f"/{safe}", f"/console/static/{safe}", "/incidents", "/healthz"):
        try:
            response = client.get(target, headers={k: v.encode("ascii", "ignore").decode() for k, v in headers.items()})
        except UnicodeError:
            continue
        _no_server_error(response)
