"""The approval log is a hash chain: tampering with it, or with the rows it describes, is reported."""

import uuid

import pytest
from sqlalchemy import delete, update

from src.db import repositories
from src.db.client import session_scope
from src.db.models import AuditEntry, RemediationApproval
from src.services import audit_chain
from tests.unit.conftest import REPORTER_HEADERS
from tests.unit.test_approval import decide, proposal  # noqa: F401  (fixture)


def _chain(events):
    """Build a valid chain from (approval_id, event, detail) triples."""
    entries, prev = [], audit_chain.GENESIS
    for seq, (approval_id, event, detail) in enumerate(events, start=1):
        entry = {
            "seq": seq,
            "approval_id": approval_id,
            "event": event,
            "actor": "bob",
            "patch_sha256": "ab" * 32,
            "detail": detail,
            "at": f"2026-01-01T10:0{seq}:00+00:00",
            "prev_hash": prev,
        }
        entry["entry_hash"] = audit_chain.entry_hash(prev, entry)
        entries.append(entry)
        prev = entry["entry_hash"]
    return entries


THREE = [("a1", "pending", ""), ("a1", "approved", "looks right"), ("a1", "executed", "")]


def test_valid_chain_is_intact_and_has_a_head():
    entries = _chain(THREE)
    result = audit_chain.verify(entries)
    assert result["status"] == "intact" and result["entries"] == 3 and result["head"] == entries[-1]["entry_hash"]


def test_empty_chain_is_none_not_intact():
    assert audit_chain.verify([])["status"] == "none"


def test_edited_entry_is_found_at_that_entry():
    entries = _chain(THREE)
    entries[1]["detail"] = "rubber stamp"
    result = audit_chain.verify(entries)
    assert result["status"] == "broken" and result["broken_at"] == 2 and "hash" in result["reason"]


def test_deleted_entry_breaks_the_chain():
    entries = _chain(THREE)
    del entries[1]
    result = audit_chain.verify(entries)
    assert result["status"] == "broken" and result["broken_at"] == 2


def test_deleting_the_last_entry_is_not_detectable_without_the_head():
    entries = _chain(THREE)
    kept = audit_chain.verify(entries[:2])
    assert kept["status"] == "intact" and kept["head"] != audit_chain.verify(entries)["head"]  # only the head tells


def test_relinked_entry_is_found():
    entries = _chain(THREE)
    entries[2]["prev_hash"] = "f" * 64
    assert audit_chain.verify(entries)["status"] == "broken"


def test_entries_are_ordered_by_seq_not_by_input_order():
    entries = _chain(THREE)
    assert audit_chain.verify(list(reversed(entries)))["status"] == "intact"


def test_crosscheck_flags_status_changed_in_place():
    entries = _chain(THREE[:2])
    approvals = [{"approval_id": "a1", "status": "rejected", "created_at": "2026-01-01T10:01:00+00:00"}]
    result = audit_chain.check(entries, approvals)
    assert result["status"] == "inconsistent" and "'rejected'" in result["reason"] and "'approved'" in result["reason"]


def test_crosscheck_flags_decided_approval_missing_from_chain():
    approvals = [
        {"approval_id": "a1", "status": "approved", "created_at": "2026-01-01T10:01:00+00:00"},
        {"approval_id": "a2", "status": "approved", "created_at": "2026-01-02T10:00:00+00:00"},
    ]
    result = audit_chain.check(_chain(THREE[:2]), approvals)
    assert result["status"] == "inconsistent" and "a2" in result["reason"]


def test_approval_older_than_the_chain_is_not_a_mismatch():
    approvals = [{"approval_id": "old", "status": "executed", "created_at": "2025-12-31T10:00:00"}]  # naive = UTC
    assert (
        audit_chain.check(
            _chain(THREE),
            [*approvals, {"approval_id": "a1", "status": "executed", "created_at": "2026-01-01T10:01:00"}],
        )["status"]
        == "intact"
    )


def test_broken_chain_is_not_crosschecked():
    entries = _chain(THREE)
    entries[0]["actor"] = "mallory"
    assert audit_chain.check(entries, [])["status"] == "broken"


def test_hash_does_not_depend_on_extra_keys_or_key_order():
    entry = _chain(THREE)[0]
    shuffled = dict(reversed(list(entry.items())), extra="ignored")
    assert audit_chain.entry_hash(entry["prev_hash"], shuffled) == entry["entry_hash"]


# ------------------------------------------------------------------ against the database


def _incident_id(incident):
    return uuid.UUID(incident["incident_id"])


def test_lifecycle_writes_a_chain_that_verifies(client, proposal):  # noqa: F811
    incident, _ = proposal
    decide(client, incident)
    entries = repositories.list_audit_entries(_incident_id(incident))
    assert [e["event"] for e in entries] == ["pending", "approved", "executed"]
    assert entries[1]["actor"] == "bob" and entries[0]["actor"] == "agent"
    body = client.get(f"/incidents/{incident['incident_id']}/audit", headers=REPORTER_HEADERS).json()
    assert body["chain"]["status"] == "intact" and body["chain"]["entries"] == 3
    assert body["chain"]["head"] == entries[-1]["entry_hash"]


def test_rejection_is_in_the_chain_with_its_note(client, proposal):  # noqa: F811
    incident, _ = proposal
    decide(client, incident, "reject", note="wrong layer")
    entries = repositories.list_audit_entries(_incident_id(incident))
    assert [e["event"] for e in entries] == ["pending", "rejected"] and entries[1]["detail"] == "wrong layer"


def test_a_refused_decision_leaves_no_entry(client, proposal):  # noqa: F811
    incident, _ = proposal
    decide(client, incident, patch_sha256="0" * 64)  # stale hash: refused
    assert [e["event"] for e in repositories.list_audit_entries(_incident_id(incident))] == ["pending"]


def test_newer_proposal_supersedes_and_both_are_recorded():
    incident_id = uuid.uuid4()
    with session_scope() as session:
        from src.db.models import Incident

        session.add(
            Incident(
                id=incident_id,
                fingerprint="fp",
                repo_name="o/r",
                error_message="e",
                stack_trace="t",
                status="awaiting_approval",
            )
        )
    first = repositories.create_approval(incident_id, "a.py", "aa" * 32, 24)
    second = repositories.create_approval(incident_id, "a.py", "bb" * 32, 24)
    entries = repositories.list_audit_entries(incident_id)
    assert [(e["approval_id"], e["event"]) for e in entries] == [
        (first["approval_id"], "pending"),
        (first["approval_id"], "superseded"),
        (second["approval_id"], "pending"),
    ]
    assert audit_chain.check(entries, repositories.list_approvals(incident_id))["status"] == "intact"


@pytest.mark.parametrize("tamper", ["edit_entry", "delete_entry", "flip_status"])
def test_database_tampering_is_reported(client, proposal, tamper):  # noqa: F811
    incident, _ = proposal
    decide(client, incident, "reject", note="no")
    incident_id = _incident_id(incident)
    with session_scope() as session:
        if tamper == "edit_entry":
            session.execute(update(AuditEntry).where(AuditEntry.event == "rejected").values(detail="fine"))
        elif tamper == "delete_entry":
            session.execute(delete(AuditEntry).where(AuditEntry.event == "pending"))
        else:
            session.execute(update(RemediationApproval).values(status="approved"))
    body = client.get(f"/incidents/{incident['incident_id']}/audit", headers=REPORTER_HEADERS).json()
    assert body["chain"]["status"] in ("broken", "inconsistent")
    assert audit_chain.main(["--incident", str(incident_id)]) == 1


def test_cli_reports_clean_database(client, proposal, capsys):  # noqa: F811
    incident, _ = proposal
    decide(client, incident)
    assert audit_chain.main([]) == 0
    out = capsys.readouterr().out
    assert incident["incident_id"] in out and "intact" in out and "0 with a problem" in out


def test_incident_without_a_chain_is_reported_as_none(client, fake_llm, source_fetch):
    from tests.unit.conftest import API_HEADERS
    from tests.unit.factories import incident_payload, make_analysis

    fake_llm([make_analysis()])
    created = client.post("/webhook/incident?wait=true", json=incident_payload(), headers=API_HEADERS).json()
    body = client.get(f"/incidents/{created['incident_id']}/audit", headers=REPORTER_HEADERS).json()
    assert body["chain"]["status"] == "none"
