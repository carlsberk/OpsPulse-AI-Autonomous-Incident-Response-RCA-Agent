"""Evaluation runner for the RCA workflow.

    python -m evals.run_rca --mode mock                  # deterministic, no credentials, used in CI
    python -m evals.run_rca --mode live                  # real Groq model, needs GROQ_API_KEY
    python -m evals.run_rca --mode mock --min structured_output_validity=0.9

Writes evals/reports/rca-<mode>-<UTC timestamp>.json (machine readable) and .md (summary).
"""

from __future__ import annotations

import argparse
import json
import os
import pathlib
import re
import sys
import time
from datetime import UTC, datetime
from typing import Any

os.environ.setdefault("DATABASE_URL", "sqlite://")  # the workflow under evaluation never touches the database

from evals.dataset import DATASETS, load_rca_dataset
from evals.harness import ScriptedModel, run_case
from evals.metrics import compute_metrics
from src.config import settings
from src.versions import run_metadata

REPORTS = pathlib.Path(__file__).with_name("reports")
DATASET_FILES = {
    "tuning": DATASETS / "rca_cases.json",  # used while developing prompts and the gate
    "holdout": DATASETS / "rca_holdout.json",  # never used for tuning
    "adversarial": DATASETS / "rca_adversarial.json",  # tries to provoke unsupported conclusions
    "callers": DATASETS / "rca_callers.json",  # root cause in a calling function
    "reviewer_memory": DATASETS / "rca_reviewer_memory.json",  # an earlier reviewer's note says how to fix
}


def _summary(report: dict[str, Any]) -> str:
    m = report["metrics"]
    lines = [
        f"# RCA evaluation - {report['mode']} mode",
        "",
        f"- Run at: {report['started_at']}  |  dataset: {report['dataset_version']} ({m['cases']} cases)",
        f"- Versions: {json.dumps(report['run_metadata'])}",
    ]
    if report["mode"] == "mock":
        lines.append(
            "- **Mock mode: replies are scripted. These numbers describe the evaluator and harness, "
            "not model quality.**"
        )
    lines += ["", "| Metric | Value | n/d | Definition |", "|---|---|---|---|"]
    for name, value in m.items():
        if isinstance(value, dict) and "definition" in value:
            shown = "n/a" if value["value"] is None else f"{value['value']:.3f}"
            lines.append(f"| {name} | {shown} | {value['numerator']}/{value['denominator']} | {value['definition']} |")
    lines += [
        "",
        f"- Average attempts: {m['avg_attempts']}",
        f"- Not evaluated (provider prevented completion): {m['cases_not_evaluated'] or 'none'}",
        f"- LLM latency ms: {m['llm_latency_ms']}  |  case wall ms: {m['case_wall_ms']}",
        f"- Tokens: {m['tokens']}  |  estimated cost USD: {m['estimated_cost_usd']}",
        "",
        "| Case | Expected | Predicted | Gate | Status | Attempts | Quotes ok |",
        "|---|---|---|---|---|---|---|",
    ]
    for r in report["results"]:
        expected = r["expected"]["root_cause_category"] + (" (inconclusive)" if r["expected"]["inconclusive"] else "")
        lines.append(
            f"| {r['case_id']} | {expected} | {r['predicted_category']} | {'pass' if r['gate_passed'] else 'fail'} | "
            f"{r['workflow_status']} | {len(r['attempts'])} | {r['grounded_quotes']}/{r['observed_quotes']} |"
        )
    consistency = m.get("consistency")
    if consistency:
        stable = consistency["stable_outcome_rate"]
        lines += [
            "",
            f"### Run-to-run consistency ({consistency['cases_repeated']} repeated case(s); stable outcome "
            f"{stable['numerator']}/{stable['denominator']})",
            "",
            "| Case | Runs | Accepted | Correct category | Distinct outcomes |",
            "|---|---|---|---|---|",
        ]
        for case_id, row in consistency["per_case"].items():
            lines.append(
                f"| {case_id} | {row['runs']} | {row['accepted']} | {row['correct_category']} | "
                f"{row['distinct_outcomes']} |"
            )
    return "\n".join(lines) + "\n"


def run(
    mode: str,
    case_ids: list[str] | None = None,
    out_dir: pathlib.Path = REPORTS,
    sleep_seconds: float = 1.0,
    dataset_name: str = "tuning",
    repeat: int = 1,
) -> dict[str, Any]:
    if mode == "live" and not settings.groq_api_key:
        raise SystemExit("live mode needs GROQ_API_KEY")
    dataset = load_rca_dataset(DATASET_FILES[dataset_name])
    cases = [c for c in dataset.cases if not case_ids or c.id in case_ids]
    started = datetime.now(UTC)
    results = []
    for case in cases:
        for index in range(max(repeat, 1)):
            factory = None
            if mode == "mock":
                model = ScriptedModel(case)
                factory = lambda temperature, model=model: model  # noqa: E731
            results.append({**run_case(case, factory), "run": index + 1})
            if mode == "live":
                time.sleep(sleep_seconds)  # stay inside provider rate limits (tokens per minute)
    report = {
        "mode": mode,
        "started_at": started.isoformat(),
        "dataset_version": dataset.version,
        "run_metadata": run_metadata(),
        "metrics": compute_metrics(results, settings.llm_cost_input_per_mtok, settings.llm_cost_output_per_mtok),
        "results": results,
    }
    out_dir.mkdir(parents=True, exist_ok=True)
    model_tag = "mock" if mode == "mock" else re.sub(r"[^A-Za-z0-9-]+", "_", settings.model_name)
    # Model names may contain dots (qwen3.8-27b), so never derive the paths with Path.with_suffix.
    stem = f"rca-{dataset_name}-{mode}-{model_tag}-{started.strftime('%Y%m%dT%H%M%SZ')}"
    json_path, md_path = out_dir / f"{stem}.json", out_dir / f"{stem}.md"
    json_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    md_path.write_text(_summary(report), encoding="utf-8")
    report["paths"] = [str(json_path), str(md_path)]
    return report


def _check_minimums(metrics: dict[str, Any], minimums: list[str]) -> list[str]:
    failures = []
    for item in minimums:
        name, _, raw = item.partition("=")
        value = (metrics.get(name) or {}).get("value")
        if value is None or value < float(raw):
            failures.append(f"{name}={value} < {raw}")
    return failures


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--mode", choices=("mock", "live"), default="mock")
    parser.add_argument("--dataset", choices=tuple(DATASET_FILES), default="tuning")
    parser.add_argument(
        "--dataset-file", type=pathlib.Path, help="run an external dataset file (e.g. a real-repo pilot)"
    )
    parser.add_argument("--case", action="append", dest="cases", help="run only this case id (repeatable)")
    parser.add_argument("--out", type=pathlib.Path, default=REPORTS)
    parser.add_argument("--sleep", type=float, default=1.0, help="seconds between cases in live mode")
    parser.add_argument(
        "--repeat", type=int, default=1, help="run every case N times and report run-to-run consistency"
    )
    parser.add_argument(
        "--min",
        action="append",
        default=[],
        metavar="METRIC=VALUE",
        help="fail (exit 1) if a ratio metric is below VALUE",
    )
    parser.add_argument(
        "--rescore",
        type=pathlib.Path,
        metavar="REPORT_JSON",
        help="recompute metrics for a saved report with the current definitions (no model calls)",
    )
    args = parser.parse_args(argv)
    if args.rescore:
        report = json.loads(args.rescore.read_text(encoding="utf-8"))
        report["metrics"] = compute_metrics(
            report["results"], settings.llm_cost_input_per_mtok, settings.llm_cost_output_per_mtok
        )
        report["rescored_at"] = datetime.now(UTC).isoformat()
        args.rescore.write_text(json.dumps(report, indent=2), encoding="utf-8")
        args.rescore.with_suffix(".md").write_text(_summary(report), encoding="utf-8")
        print(_summary(report))
        return 0
    dataset_name = args.dataset
    if args.dataset_file:
        dataset_name = args.dataset_file.stem
        DATASET_FILES[dataset_name] = args.dataset_file
    report = run(args.mode, args.cases, args.out, args.sleep, dataset_name, args.repeat)
    print(_summary(report))
    print("reports:", *report["paths"], sep="\n  ")
    failures = _check_minimums(report["metrics"], args.min)
    for failure in failures:
        print("THRESHOLD FAILED:", failure, file=sys.stderr)
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
