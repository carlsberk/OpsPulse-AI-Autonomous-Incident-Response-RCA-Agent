"""Per-case-class reliability report with version-to-version change, from saved evaluation reports.

    python -m evals.reliability_report CANDIDATE.json [--baseline BASELINE.json] [--out report.md]

A single overall percentage hides which cases moved. This groups runs by case class (control / caller / adversarial
/ other), reports acceptance, correct category, patch kept, false acceptance and run-to-run stability, then lists
every case whose outcome differs from the baseline. Latency and tokens are reported next to accuracy, never instead.
"""

from __future__ import annotations

import argparse
import json
import pathlib
import statistics
from collections import defaultdict
from typing import Any


def case_class(case_id: str) -> str:
    for prefix, name in (("control", "control"), ("caller", "caller"), ("adv", "adversarial")):
        if case_id.startswith(prefix):
            return name
    return "other"


def _is_accepted(run: dict[str, Any]) -> bool:
    return run.get("workflow_status") == "accepted"


def _correct(run: dict[str, Any]) -> bool:
    expected = (run.get("expected") or {}).get("root_cause_category")
    return expected is not None and run.get("predicted_category") == expected


def _false_acceptance(run: dict[str, Any]) -> bool:
    expected = run.get("expected") or {}
    return _is_accepted(run) and (bool(expected.get("inconclusive")) or not _correct(run))


def _tokens(run: dict[str, Any]) -> int:
    attempts = run.get("attempts") or []
    return sum((a.get("input_tokens") or 0) + (a.get("output_tokens") or 0) for a in attempts)


def per_case(report: dict[str, Any]) -> dict[str, dict[str, Any]]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for run in report["results"]:
        grouped[run["case_id"]].append(run)
    out: dict[str, dict[str, Any]] = {}
    for case_id, runs in grouped.items():
        outcomes = {(_is_accepted(r), bool(r.get("patch")), r.get("predicted_category")) for r in runs}
        out[case_id] = {
            "class": case_class(case_id),
            "runs": len(runs),
            "accepted": sum(_is_accepted(r) for r in runs),
            "correct": sum(_correct(r) for r in runs),
            "patch_kept": sum(bool(r.get("patch")) for r in runs),
            "false_accepted": sum(_false_acceptance(r) for r in runs),
            "stable": len(outcomes) == 1,
            "latency_ms": statistics.fmean(r.get("wall_ms") or 0 for r in runs),
            "tokens": statistics.fmean(_tokens(r) for r in runs),
        }
    return out


def per_class(cases: dict[str, dict[str, Any]]) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for name in sorted({c["class"] for c in cases.values()}):
        rows = [c for c in cases.values() if c["class"] == name]
        runs = sum(c["runs"] for c in rows)
        out[name] = {
            "cases": len(rows),
            "runs": runs,
            **{k: sum(c[k] for c in rows) for k in ("accepted", "correct", "patch_kept", "false_accepted")},
            "stable": sum(c["stable"] for c in rows),
            "latency_ms": sum(c["latency_ms"] * c["runs"] for c in rows) / runs,
            "tokens": sum(c["tokens"] * c["runs"] for c in rows) / runs,
        }
    return out


def changes(base: dict[str, dict[str, Any]], cand: dict[str, dict[str, Any]]) -> list[str]:
    lines = []
    for case_id in sorted(set(base) & set(cand)):
        b, c = base[case_id], cand[case_id]
        diffs = []
        for key, label in (("accepted", "accepted"), ("correct", "correct category"), ("patch_kept", "patch kept")):
            if b[key] / b["runs"] != c[key] / c["runs"]:
                diffs.append(f"{label} {b[key]}/{b['runs']} -> {c[key]}/{c['runs']}")
        if diffs:
            lines.append(f"- `{case_id}` ({c['class']}): " + "; ".join(diffs))
    return lines


def render(candidate: dict[str, Any], baseline: dict[str, Any] | None, source: str) -> str:
    meta = candidate["run_metadata"]
    cases = per_case(candidate)
    out = [
        f"# Reliability report - {candidate['dataset_version']}",
        "",
        f"- Run: `{source}`, started {candidate['started_at']}, mode `{candidate['mode']}`",
        f"- Model `{meta.get('model')}`, prompt `{meta.get('prompt_version')}`, "
        f"quality gate `{meta.get('evaluator_version')}`, retrieval `{meta.get('retrieval_strategy')}`",
        "",
        "## By case class",
        "",
        "| class | cases | runs | accepted | correct category | patch kept | false accepted | stable cases "
        "| mean latency ms | mean tokens |",
        "|---|---|---|---|---|---|---|---|---|---|",
    ]
    for name, c in per_class(cases).items():
        out.append(
            f"| {name} | {c['cases']} | {c['runs']} | {c['accepted']}/{c['runs']} | {c['correct']}/{c['runs']} "
            f"| {c['patch_kept']}/{c['runs']} | {c['false_accepted']}/{c['runs']} | {c['stable']}/{c['cases']} "
            f"| {c['latency_ms']:.0f} | {c['tokens']:.0f} |"
        )
    out += ["", "## By case", "", "| case | class | runs | accepted | correct | patch kept | stable |"]
    out.append("|---|---|---|---|---|---|---|")
    for case_id, c in sorted(cases.items()):
        out.append(
            f"| {case_id} | {c['class']} | {c['runs']} | {c['accepted']} | {c['correct']} "
            f"| {c['patch_kept']} | {'yes' if c['stable'] else 'no'} |"
        )
    if baseline is not None:
        bmeta = baseline["run_metadata"]
        out += [
            "",
            f"## Change against baseline (`{bmeta.get('prompt_version')}` / `{bmeta.get('evaluator_version')}`)",
            "",
        ]
        if baseline["dataset_version"] != candidate["dataset_version"]:
            out.append("Datasets differ - not compared.")
        else:
            out += changes(per_case(baseline), cases) or ["No case changed its acceptance, category or patch rate."]
    out += ["", "Counts are runs, not percentages: with a few cases per class one run moves a rate a lot."]
    return "\n".join(out) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("candidate", type=pathlib.Path)
    parser.add_argument("--baseline", type=pathlib.Path)
    parser.add_argument("--out", type=pathlib.Path)
    args = parser.parse_args(argv)
    candidate = json.loads(args.candidate.read_text(encoding="utf-8"))
    baseline = json.loads(args.baseline.read_text(encoding="utf-8")) if args.baseline else None
    text = render(candidate, baseline, args.candidate.name)
    if args.out:
        args.out.write_text(text, encoding="utf-8")
    else:
        print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
