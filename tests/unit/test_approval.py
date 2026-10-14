"""Human approval before any GitHub write: binding, staleness, rejection, expiry and no double execution."""

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import update

from src.db.client import session_scope
from src.db.models import RemediationApproval
from src.integrations.github import PullRequestResult
from src.services import remediation_service
from tests.unit.conftest import API_HEADERS, REPORTER_HEADERS, REVIEWER_HEADERS
from tests.unit.factories import incident_payload, make_analysis


@pytest.fixture
def proposal(client, fake_llm, source_fetch, set_settings, monkeypatch):
    """An accepted analysis waiting for approval; PR creation is captured, not sent to GitHub."""
    set_settings(enable_github_remediation=True, github_token="test-token", require_remediation_approval=True)
    created = []

    def fake_create(**kwargs):
        created.append(kwargs)
        return PullRequestResult("https://github.com/o/r/pull/11", 11, "opspulse/fix-x", already_existed=False)

    monkeypatch.setattr(remediation_service, "create_fix_pull_request", fake_create)
    fake_llm([make_analysis()])
    body = client.post("/webhook/incident?wait=true", json=incident_payload(), headers=API_HEADERS).json()
    return body, created


def decide(client, incident, decision="approve", headers=REVIEWER_HEADERS, **overrides):
    approval = incident["pending_approval"]
    payload = {
        "approval_id": approval["approval_id"],
        "patch_sha256": approval["patch_sha256"],
        "decision": decision,
        **overrides,
    }
    return client.post(f"/incidents/{incident['incident_id']}/remediation/decision", json=payload, headers=headers)


def test_accepted_analysis_waits_for_approval_and_nothing_is_sent(proposal):
    incident, created = proposal
    assert incident["status"] == "awaiting_approval" and not created
    pending = incident["pending_approval"]
    assert pending["target_file"] == "src/app.py"
    assert pending["patch_sha256"] == remediation_service.patch_sha256(incident["suggested_patch"])


def test_approval_opens_exactly_one_pr(client, proposal):
    incident, created = proposal
    resp = decide(client, incident)
    assert resp.status_code == 200 and resp.json()["status"] == "pr_created"
    assert len(created) == 1 and "Approved for PR creation by:** bob" in created[0]["body"]
    again = decide(client, incident)  # resumed / repeated approval
    assert again.status_code == 409 and len(created) == 1
    assert resp.json()["pending_approval"] is None


def test_rejection_is_final_and_has_no_side_effect(client, proposal):
    incident, created = proposal
    resp = decide(client, incident, "reject", note="fix is in the wrong layer")
    body = resp.json()
    assert body["status"] == "remediation_rejected" and body["error_category"] == "approval_rejected"
    assert decide(client, incident).status_code == 409 and not created


def test_wrong_patch_hash_is_rejected_as_stale(client, proposal):
    incident, created = proposal
    resp = decide(client, incident, patch_sha256="0" * 64)
    assert resp.status_code == 409 and "patch_sha256" in resp.json()["error"]["message"] and not created


def test_approval_cannot_be_used_for_another_incident(client, proposal):
    incident, created = proposal
    other = dict(incident, incident_id=str(uuid.uuid4()))
    assert decide(client, other).status_code == 404
    unknown = dict(incident, pending_approval={**incident["pending_approval"], "approval_id": str(uuid.uuid4())})
    assert decide(client, unknown).status_code == 404 and not created


def test_expired_approval_is_not_consent(client, proposal):
    incident, created = proposal
    with session_scope() as session:
        session.execute(update(RemediationApproval).values(expires_at=datetime.now(UTC) - timedelta(minutes=1)))
    resp = decide(client, incident)
    assert resp.status_code == 409 and "expired" in resp.json()["error"]["message"] and not created


def test_configuration_change_before_approval_blocks_the_side_effect(client, proposal, set_settings):
    incident, created = proposal
    set_settings(enable_github_remediation=False)
    resp = decide(client, incident)
    assert resp.status_code == 200 and resp.json()["status"] == "analysis_ready" and not created


def test_decision_requires_authentication_and_valid_body(client, proposal):
    incident, _ = proposal
    url = f"/incidents/{incident['incident_id']}/remediation/decision"
    assert client.post(url, json={}).status_code == 401
    bad = {"approval_id": str(uuid.uuid4()), "patch_sha256": "xyz", "decision": "maybe"}
    assert client.post(url, json=bad, headers=REVIEWER_HEADERS).status_code == 422


def test_reporters_cannot_decide(client, proposal):
    incident, created = proposal
    resp = decide(client, incident, headers=REPORTER_HEADERS)
    assert resp.status_code == 403 and "role 'reporter'" in resp.json()["error"]["message"] and not created


def test_four_eyes_submitter_cannot_approve_own_incident(client, fake_llm, proposal):
    _, created = proposal
    fake_llm([make_analysis()])
    own = client.post(
        "/webhook/incident?wait=true", json=incident_payload(error_message="TypeError: other"), headers=REVIEWER_HEADERS
    ).json()
    assert own["submitted_by"] == "bob" and own["status"] == "awaiting_approval"
    resp = decide(client, own)  # bob deciding on bob's own incident
    assert resp.status_code == 403 and "may not decide" in resp.json()["error"]["message"] and not created


def test_reviewer_identity_is_recorded_from_the_key(client, proposal):
    from src.db import repositories

    incident, _ = proposal
    assert incident["submitted_by"] == "default"
    decide(client, incident, "reject")
    approval = repositories.get_approval(uuid.UUID(incident["pending_approval"]["approval_id"]))
    assert approval["reviewer"] == "bob" and approval["status"] == "rejected"


def test_simultaneous_approvals_open_exactly_one_pull_request(client, proposal):
    """Eight reviewers press 'approve' at the same moment: the compare-and-set claim lets exactly one through."""
    import threading

    from fastapi.testclient import TestClient

    from src.main import app

    incident, created = proposal
    barrier = threading.Barrier(8)
    statuses: list[int] = []
    lock = threading.Lock()

    def approve() -> None:
        local = TestClient(app)
        barrier.wait(timeout=10)
        code = decide(local, incident).status_code
        with lock:
            statuses.append(code)

    threads = [threading.Thread(target=approve) for _ in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=60)

    assert sorted(statuses).count(200) == 1, statuses  # one winner
    assert all(code in (200, 409) for code in statuses), statuses  # everyone else gets a conflict, never a 5xx
    assert len(created) == 1  # and exactly one GitHub write
