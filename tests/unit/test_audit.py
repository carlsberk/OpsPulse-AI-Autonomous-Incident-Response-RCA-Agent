"""Review timeline: facts only, in order, including rejected and superseded proposals."""

import uuid

from src.services.audit import build_audit
from tests.unit.conftest import API_HEADERS, REPORTER_HEADERS
from tests.unit.factories import incident_payload, make_analysis


def test_timeline_orders_events_and_attributes_decisions():
    incident = {
        "incident_id": "i",
        "repo_name": "o/r",
        "created_at": "2026-01-01T10:00:00+00:00",
        "updated_at": "2026-01-01T10:05:00+00:00",
        "submitted_by": "ci-bot",
        "quality_score": 0.93,
        "iterations": 1,
        "analysis": {"execution_metadata": {"model": "m", "prompt_version": "p", "evaluator_version": "g"}},
        "pr_url": "https://github.com/o/r/pull/1",
        "pr_branch": "opspulse/fix-1",
    }
    approvals = [
        {
            "status": "superseded",
            "created_at": "2026-01-01T10:01:00+00:00",
            "decided_at": "2026-01-01T10:02:00+00:00",
            "target_file": "a.py",
            "patch_sha256": "aa",
            "expires_at": "x",
        },
        {
            "status": "executed",
            "created_at": "2026-01-01T10:02:00+00:00",
            "decided_at": "2026-01-01T10:30:00+00:00",
            "reviewer": "maria",
            "decision_note": "looks right",
            "target_file": "a.py",
            "patch_sha256": "bb",
            "expires_at": "x",
        },
    ]
    audit = build_audit(incident, approvals)
    names = [e["event"] for e in audit["events"]]
    assert names == ["submitted", "analysed", "proposed", "superseded", "proposed", "approved", "pr_opened"]
    approved = next(e for e in audit["events"] if e["event"] == "approved")
    assert approved["actor"] == "maria" and approved["detail"] == "looks right"
    assert audit["events"][0]["actor"] == "ci-bot" and audit["merge_policy"] == "never automatic"


def test_incident_without_analysis_has_only_submission():
    audit = build_audit({"incident_id": "i", "repo_name": "o/r", "created_at": "t"}, [])
    assert [e["event"] for e in audit["events"]] == ["submitted"]


def test_endpoint(client, fake_llm, source_fetch):
    fake_llm([make_analysis()])
    resp = client.post("/webhook/incident?wait=true", json=incident_payload(), headers=API_HEADERS)
    incident_id = resp.json()["incident_id"]
    body = client.get(f"/incidents/{incident_id}/audit", headers=REPORTER_HEADERS).json()
    assert body["events"][0]["event"] == "submitted"
    assert client.get(f"/incidents/{uuid.uuid4()}/audit", headers=REPORTER_HEADERS).status_code == 404
