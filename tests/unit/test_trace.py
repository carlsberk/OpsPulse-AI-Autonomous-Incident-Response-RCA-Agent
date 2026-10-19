"""Run trace of one incident: versions, per-attempt latency/tokens and gate verdicts from the stored record."""

import uuid

from src.services.trace import build_trace
from tests.unit.conftest import API_HEADERS, REPORTER_HEADERS
from tests.unit.factories import incident_payload, make_analysis


def test_trace_endpoint_reports_versions_attempts_and_totals(client, fake_llm, source_fetch):
    fake_llm([make_analysis()])
    resp = client.post("/webhook/incident?wait=true", json=incident_payload(), headers=API_HEADERS)
    incident_id = resp.json()["incident_id"]

    trace = client.get(f"/incidents/{incident_id}/trace", headers=REPORTER_HEADERS)
    assert trace.status_code == 200, trace.text
    body = trace.json()
    assert body["incident_id"] == incident_id
    assert body["versions"]["prompt_version"] and body["versions"]["evaluator_version"]
    assert body["totals"]["llm_calls"] == len(body["attempts"]) >= 1
    assert body["totals"]["estimated_cost_usd"] is None
    assert all("gate_passed" in a and "failed_checks" in a for a in body["attempts"])


def test_trace_of_unknown_incident_is_404(client):
    resp = client.get(f"/incidents/{uuid.uuid4()}/trace", headers=REPORTER_HEADERS)
    assert resp.status_code == 404


def test_trace_tolerates_a_record_without_analysis():
    trace = build_trace({"incident_id": "x", "status": "queued", "analysis": None})
    assert trace["attempts"] == [] and trace["totals"]["latency_ms"] is None and trace["request_id"] is None


def test_totals_sum_attempts_and_ignore_missing_values():
    analysis = {
        "attempts": [
            {"iteration": 1, "latency_ms": 100, "input_tokens": 10, "output_tokens": 5, "gate_passed": False},
            {"iteration": 2, "latency_ms": 50, "input_tokens": None, "output_tokens": 7, "gate_passed": True},
        ],
    }
    trace = build_trace({"status": "analysis_ready", "analysis": analysis, "request_id": "abc123"})
    assert trace["request_id"] == "abc123"
    assert trace["totals"] == {
        "llm_calls": 2,
        "latency_ms": 150,
        "input_tokens": 10,
        "output_tokens": 12,
        "estimated_cost_usd": None,
    }


def test_request_id_is_stored_when_the_incident_is_submitted(client, fake_llm, source_fetch):
    fake_llm([make_analysis()])
    headers = {**API_HEADERS, "X-Request-ID": "req-abc-123"}
    resp = client.post("/webhook/incident?wait=true", json=incident_payload(), headers=headers)
    incident_id = resp.json()["incident_id"]
    assert resp.json()["request_id"] == "req-abc-123"
    assert client.get(f"/incidents/{incident_id}/trace", headers=REPORTER_HEADERS).json()["request_id"] == "req-abc-123"


def test_cost_is_estimated_only_when_both_prices_are_configured(set_settings):
    analysis = {"attempts": [{"iteration": 1, "latency_ms": 10, "input_tokens": 2000, "output_tokens": 1000}]}
    record = {"status": "analysis_ready", "analysis": analysis}
    assert build_trace(record)["totals"]["estimated_cost_usd"] is None  # no prices: no invented number
    set_settings(llm_cost_input_per_mtok=1.0)
    assert build_trace(record)["totals"]["estimated_cost_usd"] is None  # half a price list is not a price list
    set_settings(llm_cost_input_per_mtok=1.0, llm_cost_output_per_mtok=3.0)
    assert build_trace(record)["totals"]["estimated_cost_usd"] == 0.005  # (2000*1 + 1000*3) / 1e6
