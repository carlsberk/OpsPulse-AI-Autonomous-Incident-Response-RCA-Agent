"""A tamper-evident log of the approval lifecycle: every proposal and every decision is one link in a hash chain.

    python -m src.services.audit_chain [--incident <uuid>]

Each entry stores the SHA-256 of its own fields plus the hash of the entry before it (per incident), so editing or
deleting an earlier entry breaks every later one, and `verify` says where. `crosscheck` compares the chain to the
`remediation_approvals` rows, which the workflow updates in place: a row whose status was changed without a matching
chain entry is reported.

What this is, and is not: it detects edits made *after the fact* by someone who changes some rows and not all of them.
Anyone who can rewrite the whole table (a database administrator) can recompute a consistent chain; the defence is to
keep the head hash (printed here and returned by the audit endpoint) somewhere they cannot reach. Incidents decided
before this log existed have no chain and are reported as such, not as intact.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import uuid
from datetime import UTC, datetime
from typing import Any

GENESIS = "0" * 64
FIELDS = ("seq", "approval_id", "event", "actor", "patch_sha256", "detail", "at")


def entry_hash(prev_hash: str, entry: dict[str, Any]) -> str:
    """SHA-256 over the previous hash and a canonical JSON of the entry's fields (stable key order, no whitespace)."""
    body = json.dumps({k: entry.get(k) for k in FIELDS}, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(f"{prev_hash}\n{body}".encode()).hexdigest()


def _broken(count: int, seq: Any, reason: str) -> dict[str, Any]:
    return {"status": "broken", "entries": count, "head": None, "broken_at": int(seq), "reason": reason}


def verify(entries: list[dict[str, Any]]) -> dict[str, Any]:
    """Walk the chain in `seq` order. `status` is none (no entries), intact, or broken (with `broken_at`, `reason`)."""
    ordered = sorted(entries, key=lambda e: int(e["seq"]))
    if not ordered:
        return {"status": "none", "entries": 0, "head": None, "broken_at": None, "reason": "no chain recorded"}
    prev = GENESIS
    for expected_seq, entry in enumerate(ordered, start=1):
        if int(entry["seq"]) != expected_seq:
            return _broken(len(ordered), expected_seq, f"entry {expected_seq} is missing or out of order")
        if entry["prev_hash"] != prev:
            return _broken(len(ordered), entry["seq"], "does not follow the previous entry")
        if entry_hash(prev, entry) != entry["entry_hash"]:
            return _broken(len(ordered), entry["seq"], "contents do not match the recorded hash")
        prev = entry["entry_hash"]
    return {"status": "intact", "entries": len(ordered), "head": prev, "broken_at": None, "reason": None}


def _when(text: Any) -> datetime:
    """An ISO timestamp as an aware UTC datetime (SQLite hands back naive ones)."""
    moment = datetime.fromisoformat(str(text))
    return moment if moment.tzinfo else moment.replace(tzinfo=UTC)


def crosscheck(entries: list[dict[str, Any]], approvals: list[dict[str, Any]]) -> list[str]:
    """Approval rows whose current status the chain does not explain (rows are updated in place; the chain is not)."""
    if not entries:
        return []
    last_event: dict[str, str] = {}
    for entry in sorted(entries, key=lambda e: int(e["seq"])):
        last_event[str(entry["approval_id"])] = str(entry["event"])
    started = min(_when(e["at"]) for e in entries)
    problems = []
    for approval in approvals:
        approval_id = str(approval["approval_id"])
        recorded = last_event.get(approval_id)
        if recorded is None:
            if _when(approval["created_at"]) >= started:
                problems.append(f"approval {approval_id} ({approval['status']}) has no chain entry")
        elif recorded != approval["status"]:
            problems.append(f"approval {approval_id} is '{approval['status']}' but the chain says '{recorded}'")
    return problems


def check(entries: list[dict[str, Any]], approvals: list[dict[str, Any]]) -> dict[str, Any]:
    """`verify` plus `crosscheck`; a chain that is internally intact but contradicts the rows is `inconsistent`."""
    result = verify(entries)
    result["mismatches"] = crosscheck(entries, approvals) if result["status"] != "broken" else []
    if result["status"] == "intact" and result["mismatches"]:
        result["status"] = "inconsistent"
        result["reason"] = result["mismatches"][0]
    return result


def main(argv: list[str] | None = None) -> int:
    from src.db import repositories

    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--incident", help="check one incident (default: every incident that has a chain)")
    args = parser.parse_args(argv)
    ids = [args.incident] if args.incident else repositories.audited_incident_ids()
    bad = 0
    for incident_id in ids:
        approvals = repositories.list_approvals(uuid.UUID(str(incident_id)))
        result = check(repositories.list_audit_entries(incident_id), approvals)
        bad += result["status"] in ("broken", "inconsistent")
        print(f"{incident_id}  {result['status']:<12} entries={result['entries']} head={result['head']}")
        if result["reason"]:
            print(f"    {result['reason']}")
    print(f"{len(ids)} incident(s) checked, {bad} with a problem")
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
