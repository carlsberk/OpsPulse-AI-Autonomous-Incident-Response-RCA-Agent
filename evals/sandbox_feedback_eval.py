"""Measure the sandbox-feedback loop on real bug-fix history: does showing the failing test output help?

    python -m evals.history_real_repo build --repo-dir CLONE --python docker:IMAGE --repo-name o/r --out history.json
    python -m evals.sandbox_feedback_eval --dataset history.json --repo-dir CLONE --python docker:IMAGE [--case ID]

Per case (live model): first attempt through the normal graph, then the real regression test on the parent commit.
If the gate accepted a patch that still fails that test, the failing output goes back to the model once, through the
same pieces the service uses (`build_feedback` and the repair graph), and the new patch is tested again. A repaired
patch only counts when the gate accepts it. This is the tests-by-hand version of `SANDBOX_FEEDBACK_ATTEMPTS`: the
service downloads the repository from GitHub, which a historical commit is not, so the sandbox step is replaced by
`history_real_repo.verify_patch_detailed` (same isolation: `docker:<image>`).
"""

from __future__ import annotations

import argparse
import json
import pathlib
import time
from datetime import UTC, datetime
from typing import Any, cast

from evals.dataset import load_rca_dataset
from evals.harness import run_final
from evals.history_real_repo import verify_patch_detailed
from src.agent.graph import build_repair_graph
from src.agent.sandbox_feedback import build_feedback
from src.agent.state import IncidentState
from src.config import settings
from src.integrations.sandbox import failed_tests

OUTPUT_TAIL = 2500


def _tokens(attempts: list[dict[str, Any]]) -> list[int]:
    return [sum(a.get(key) or 0 for a in attempts) for key in ("input_tokens", "output_tokens")]


def evaluate_case(case: Any, meta: dict[str, Any], repo: pathlib.Path, python: str, rounds: int) -> dict[str, Any]:
    final, _ = run_final(case, None)
    row: dict[str, Any] = {"case_id": case.id, "first_status": final["workflow_status"], "rounds": []}
    patch = final["suggested_patch"]
    verdict, detail, output = (
        verify_patch_detailed(repo, python, meta, patch) if patch else ("no_patch", "no patch", "")
    )
    row["first_verdict"], row["first_detail"] = verdict, detail
    row["final_verdict"] = verdict
    repair = build_repair_graph()
    while len(row["rounds"]) < rounds and final["quality_gate_passed"] and verdict == "fails_tests":
        feedback = build_feedback(
            {
                "status": "still_failing",
                "summary": "the repository's tests still fail after the patch",
                "after": {"failed": list(failed_tests(output)), "output_tail": output[-OUTPUT_TAIL:]},
            }
        )
        state = cast(IncidentState, {**final, "previous_feedback": feedback, "workflow_status": "running"})
        repaired = cast(IncidentState, repair.invoke(state))
        entry: dict[str, Any] = {"gate_passed": repaired["quality_gate_passed"]}
        row["rounds"].append(entry)
        new_patch = repaired["suggested_patch"]
        final = cast(IncidentState, {**repaired})
        if not (repaired["quality_gate_passed"] and new_patch and new_patch != patch):
            entry["verdict"] = "no_new_patch"
            break
        patch = new_patch
        verdict, detail, output = verify_patch_detailed(repo, python, meta, patch)
        entry["verdict"], entry["detail"] = verdict, detail
        row["final_verdict"] = verdict
    row["tokens_in_out"] = _tokens(final["attempts"])
    row["attempts"] = len(final["attempts"])
    return row


def run(args: argparse.Namespace) -> dict[str, Any]:
    if not settings.groq_api_key:
        raise SystemExit("this evaluation needs GROQ_API_KEY (live model)")
    dataset = load_rca_dataset(args.dataset)
    metas = json.loads(args.dataset.with_suffix(".meta.json").read_text())
    cases = [c for c in dataset.cases if not args.case or c.id in args.case][: args.max_cases]
    rows = []
    for case in cases:
        rows.append(evaluate_case(case, metas[case.id], args.repo_dir, args.python, args.rounds))
        time.sleep(args.sleep)
    summary: dict[str, int] = {}
    for row in rows:
        key = f"{row['first_verdict']} -> {row['final_verdict']}"
        summary[key] = summary.get(key, 0) + 1
    return {"started_at": datetime.now(UTC).isoformat(), "dataset": args.dataset.name, "rows": rows, "summary": summary}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--dataset", type=pathlib.Path, required=True)
    parser.add_argument("--repo-dir", type=pathlib.Path, required=True)
    parser.add_argument("--python", required=True, help="docker:<image> (isolated) or a host interpreter")
    parser.add_argument("--case", action="append", help="case id (repeatable); default: all")
    parser.add_argument("--max-cases", type=int, default=1000)
    parser.add_argument("--rounds", type=int, default=1, help="feedback rounds per case")
    parser.add_argument("--sleep", type=float, default=20.0, help="seconds between cases (provider rate limits)")
    parser.add_argument("--out", type=pathlib.Path, default=pathlib.Path("evals/reports"))
    args = parser.parse_args(argv)
    report = run(args)
    args.out.mkdir(parents=True, exist_ok=True)
    path = args.out / f"sandbox-feedback-{datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')}.json"
    path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    for row in report["rows"]:
        print(
            f"{row['case_id']}: first={row['first_verdict']} final={row['final_verdict']} "
            f"rounds={row['rounds']} tokens_in_out={row['tokens_in_out']}"
        )
    print("summary:", json.dumps(report["summary"], sort_keys=True))
    print(f"wrote {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
