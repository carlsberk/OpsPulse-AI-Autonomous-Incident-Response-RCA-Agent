"""How good are the regression tests the model writes? Live model, real Docker sandbox.

    python -m evals.repro_eval --dataset tuning --out evals/results/repro-tuning.json --image opspulse-sandbox-test

For each case the workflow runs with the live model; if it proposes a patch AND a reproduction test, both go to the real
sandbox (no network, read-only root, caps) built from the case's own files, and the outcome is classified: the test
raised the incident's exception before the patch and passed after it (`reproduced_and_fixed`), passed on the
unpatched code (`not_reproduced`), failed for another reason, or was refused before running (`invalid`). The test text
is saved so a human can read it: a test the model wrote to pass its own fix is evidence, not proof.

The image must contain pytest (the sandbox has no network): build one with
`printf 'FROM python:3.11-slim\\nRUN pip install pytest\\n' | docker build -t opspulse-sandbox-test -`.
Cases without a patch (the model declined, or blamed a caller) are counted separately: there is nothing to run after.
"""

from __future__ import annotations

import argparse
import collections
import json
import pathlib
import time
from datetime import UTC, datetime
from typing import Any

from evals.dataset import EvalCase, load_rca_dataset
from evals.harness import run_final
from evals.run_rca import DATASET_FILES
from src.agent.reproduction import ReproductionSpec, expected_error_name
from src.config import settings
from src.integrations.sandbox import run_patch_in_sandbox
from src.versions import run_metadata


def _snapshot_of(case: EvalCase):
    def take(repo: str, dest: pathlib.Path, limit: int) -> str:
        for name, text in case.files.items():
            target = dest / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(text, encoding="utf-8")
        return "0" * 40

    return take


def evaluate_case(case: EvalCase) -> dict[str, Any]:
    final, wall_ms = run_final(case, None)
    analysis = final["root_cause_analysis"] or {}
    patch, target = final["suggested_patch"], final["affected_file"]
    test = analysis.get("reproduction_test")
    row: dict[str, Any] = {
        "case_id": case.id,
        "workflow_status": final["workflow_status"],
        "patch": bool(patch),
        "wall_ms": wall_ms,
        "reproduction_test": test,
    }
    if final["workflow_status"] == "failed":
        row["outcome"] = "not_evaluated"  # provider problem: says nothing about the model
        return row
    if not patch or not target:
        row["outcome"] = "no_patch"
        return row
    if not isinstance(test, dict):
        row["outcome"] = "no_test"
        return row
    spec = ReproductionSpec(test["path"], test["content"], expected_error_name(final["error_message"]))
    report = run_patch_in_sandbox(case.repo_name, target, patch, snapshot=_snapshot_of(case), reproduction=spec)
    reproduction = report.reproduction
    row["outcome"] = reproduction.status if reproduction else f"sandbox_{report.status}"
    row["summary"] = reproduction.summary if reproduction else report.summary
    if reproduction and reproduction.before:
        row["before_tail"] = reproduction.before.output_tail[-400:]
    return row


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--dataset", choices=tuple(DATASET_FILES), default="tuning")
    parser.add_argument(
        "--dataset-file", type=pathlib.Path, help="an external dataset (e.g. from evals.pilot_real_repo)"
    )
    parser.add_argument("--case", action="append", dest="cases")
    parser.add_argument("--image", default="opspulse-sandbox-test")
    parser.add_argument("--sleep", type=float, default=6.0)
    parser.add_argument("--out", type=pathlib.Path, required=True)
    args = parser.parse_args(argv)
    if not settings.groq_api_key:
        raise SystemExit("needs GROQ_API_KEY (live model)")
    object.__setattr__(settings, "sandbox_image", args.image)
    path = args.dataset_file or DATASET_FILES[args.dataset]
    cases = [c for c in load_rca_dataset(path).cases if not args.cases or c.id in args.cases]
    rows = []
    for case in cases:
        row = evaluate_case(case)
        rows.append(row)
        print(f"  {case.id}: {row['outcome']}")
        time.sleep(args.sleep)
    counts = collections.Counter(r["outcome"] for r in rows)
    report = {
        "run_at": datetime.now(UTC).isoformat(),
        "dataset": str(args.dataset_file.name if args.dataset_file else args.dataset),
        "run_metadata": run_metadata(),
        "counts": dict(counts),
        "rows": rows,
    }
    args.out.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    print(json.dumps(dict(counts), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
