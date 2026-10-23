"""What reviewers actually decided: the one measurement that comes from real use rather than from our datasets.

    python -m src.services.review_outcomes [--out report.md]

For every incident that got a human decision, this joins the decision to the versions that produced the analysis
(prompt, quality gate, model) and reports, per version, how many proposals were approved or rejected, how the
quality-gate score relates to the decision, and the reviewers' own words for rejections. It reads the database
(`DATABASE_URL`) and never writes. With few decisions it says so and draws no rate: a handful of approvals is an
anecdote, not an approval rate.
"""

from __future__ import annotations

import argparse
import collections
import pathlib
import statistics
from typing import Any

from sqlalchemy import select

from src.db.client import session_scope
from src.db.models import Incident, RemediationApproval
from src.retrieval.review_notes import clean_note

MIN_DECISIONS_FOR_A_RATE = 20
_APPROVED = ("approved", "executed", "execution_failed")


def collect() -> list[dict[str, Any]]:
    """One row per incident that has at least one approval row, with its latest approval's outcome."""
    rows: list[dict[str, Any]] = []
    with session_scope() as session:
        approvals = list(session.scalars(select(RemediationApproval).order_by(RemediationApproval.created_at)))
        latest: dict[Any, RemediationApproval] = {a.incident_id: a for a in approvals}  # newest wins
        for incident in session.scalars(select(Incident).where(Incident.id.in_(list(latest)))):
            approval = latest[incident.id]
            meta = ((incident.root_cause_analysis or {}).get("execution_metadata")) or {}
            rows.append(
                {
                    "prompt_version": meta.get("prompt_version") or "unknown",
                    "evaluator_version": meta.get("evaluator_version") or "unknown",
                    "model": meta.get("model") or "unknown",
                    "category": (incident.root_cause_analysis or {}).get("root_cause_category") or "unknown",
                    "quality_score": incident.quality_score,
                    "approval_status": approval.status,
                    "note": approval.decision_note or "",
                    "reviewer": approval.reviewer,
                }
            )
    return rows


def _outcome(row: dict[str, Any]) -> str:
    status = row["approval_status"]
    if status in _APPROVED:
        return "approved"
    if status == "rejected":
        return "rejected"
    return "undecided"  # pending, expired, superseded


def summarize(rows: list[dict[str, Any]]) -> str:
    """Markdown report from `collect()` rows (pure, so it can be tested without a database)."""
    decided = [r for r in rows if _outcome(r) != "undecided"]
    out = [
        "# Review outcomes",
        "",
        f"- Proposals with an approval record: {len(rows)}; decided by a human: {len(decided)}; "
        f"undecided (pending, expired, superseded): {len(rows) - len(decided)}",
    ]
    if len(decided) < MIN_DECISIONS_FOR_A_RATE:
        out.append(
            f"- **Only {len(decided)} decisions (a rate needs at least {MIN_DECISIONS_FOR_A_RATE}): counts are shown, "
            "no approval rate is claimed.**"
        )
    groups: dict[tuple[str, str, str], list[dict[str, Any]]] = collections.defaultdict(list)
    for row in rows:
        groups[(row["prompt_version"], row["evaluator_version"], row["model"])].append(row)
    out += [
        "",
        "## By version",
        "",
        "| prompt | gate | model | proposals | approved | rejected | undecided | approval rate |",
    ]
    out.append("|---|---|---|---|---|---|---|---|")
    for (prompt, gate, model), items in sorted(groups.items()):
        counts = collections.Counter(_outcome(r) for r in items)
        decided_here = counts["approved"] + counts["rejected"]
        rate = (
            f"{counts['approved']}/{decided_here}"
            if decided_here and len(decided) >= MIN_DECISIONS_FOR_A_RATE
            else "n/a"
        )
        out.append(
            f"| {prompt} | {gate} | {model} | {len(items)} | {counts['approved']} | {counts['rejected']} "
            f"| {counts['undecided']} | {rate} |"
        )
    approved = [r["quality_score"] for r in decided if _outcome(r) == "approved"]
    rejected = [r["quality_score"] for r in decided if _outcome(r) == "rejected"]
    if approved and rejected:
        out += [
            "",
            "## Does the gate score predict the decision?",
            "",
            f"- Mean gate score of approved proposals: {statistics.fmean(approved):.3f} (n={len(approved)})",
            f"- Mean gate score of rejected proposals: {statistics.fmean(rejected):.3f} (n={len(rejected)})",
            "- The gate score is a rubric score, not a probability: a gap says something about the rubric only.",
        ]
    by_category: dict[str, collections.Counter[str]] = collections.defaultdict(collections.Counter)
    for row in decided:
        by_category[row["category"]][_outcome(row)] += 1
    if by_category:
        out += [
            "",
            "## By root-cause category (decided only)",
            "",
            "| category | approved | rejected |",
            "|---|---|---|",
        ]
        for category, counts in sorted(by_category.items()):
            out.append(f"| {category} | {counts['approved']} | {counts['rejected']} |")
    notes = [(r["prompt_version"], clean_note(r["note"])) for r in decided if _outcome(r) == "rejected" and r["note"]]
    if notes:
        out += ["", "## What reviewers wrote when they rejected", ""]
        out += [f"- ({prompt}) {note}" for prompt, note in notes[:15]]
    return "\n".join(out) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--out", type=pathlib.Path)
    args = parser.parse_args(argv)
    text = summarize(collect())
    if args.out:
        args.out.write_text(text, encoding="utf-8")
    else:
        print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
