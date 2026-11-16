"""The review-outcomes report: it must not claim a rate it cannot support, and its sections must say what they count."""

from src.services import review_outcomes
from src.services.review_outcomes import MIN_DECISIONS_FOR_A_RATE, summarize


def row(status="approved", score=0.9, category="logic_error", note="", prompt="v7", gate="g9", model="m"):
    return {
        "prompt_version": prompt,
        "evaluator_version": gate,
        "model": model,
        "category": category,
        "quality_score": score,
        "approval_status": status,
        "note": note,
        "reviewer": "r",
    }


def test_few_decisions_show_counts_and_claim_no_rate():
    text = summarize([row(), row("rejected"), row("pending")])
    assert "decided by a human: 2" in text and "undecided (pending, expired, superseded): 1" in text
    assert f"at least {MIN_DECISIONS_FOR_A_RATE}" in text
    assert "| v7 | g9 | m | 3 | 1 | 1 | 1 | n/a |" in text


def test_a_rate_appears_only_once_enough_decisions_exist():
    rows = [row() for _ in range(MIN_DECISIONS_FOR_A_RATE - 4)] + [row("rejected") for _ in range(4)]
    text = summarize(rows)
    assert "no approval rate is claimed" not in text
    assert (
        f"| 20 | {MIN_DECISIONS_FOR_A_RATE - 4} | 4 | 0 | {MIN_DECISIONS_FOR_A_RATE - 4}/{MIN_DECISIONS_FOR_A_RATE} |"
        in text
    )


def test_executed_and_failed_executions_count_as_approved_and_other_statuses_as_undecided():
    text = summarize([row("executed"), row("execution_failed"), row("expired"), row("superseded")])
    assert "decided by a human: 2" in text and "undecided (pending, expired, superseded): 2" in text


def test_versions_are_reported_separately():
    text = summarize([row(prompt="v7"), row(prompt="v8", status="rejected")])
    assert "| v7 | g9 | m | 1 | 1 | 0 | 0 |" in text and "| v8 | g9 | m | 1 | 0 | 1 | 0 |" in text


def test_the_gate_score_comparison_needs_both_outcomes_and_says_it_is_not_a_probability():
    assert "Does the gate score predict" not in summarize([row(), row()])
    text = summarize([row(score=0.9), row(score=0.7), row("rejected", score=0.5)])
    assert "approved proposals: 0.800 (n=2)" in text and "rejected proposals: 0.500 (n=1)" in text
    assert "not a probability" in text


def test_categories_are_tallied_over_decided_proposals_only():
    text = summarize([row(category="a"), row("rejected", category="a"), row("pending", category="b")])
    assert "| a | 1 | 1 |" in text and "| b |" not in text


def test_rejection_notes_are_listed_with_their_prompt_version_and_approval_notes_are_not():
    text = summarize([row("rejected", note="wrong file", prompt="v7"), row(note="looks fine")])
    assert "- (v7) wrong file" in text and "looks fine" not in text


def test_no_rows_is_a_valid_report():
    text = summarize([])
    assert "Proposals with an approval record: 0" in text and "## By root-cause category" not in text


def test_main_prints_or_writes_the_report(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(review_outcomes, "collect", lambda: [row()])
    assert review_outcomes.main([]) == 0
    assert "# Review outcomes" in capsys.readouterr().out
    target = tmp_path / "report.md"
    assert review_outcomes.main(["--out", str(target)]) == 0
    assert target.read_text(encoding="utf-8").startswith("# Review outcomes")
