"""Per-case-class reliability report with version-to-version change, from saved evaluation reports.

    python -m evals.reliability_report CANDIDATE.json [--baseline BASELINE.json] [--out report.md]

A single overall percentage hides which cases moved. This groups runs by case class (control / caller / adversarial
/ other) and reports, per class and per case:

- acceptance, correct category (or an accepted alternative for an ambiguous label), patch kept, run-to-run stability;
- **false accepted** (the gate accepted a wrong or unsupported analysis) and **false rejected** (a correct, fully
  grounded diagnosis with a patch that the gate did not accept) as separate numbers, and **unsafe accepted**: an
  accepted patch whose diagnosis is wrong or that targets a file other than the labelled fix location - the most
  expensive mistake;
- evidence verification, mean attempts, latency, tokens and (only if prices were configured) cost.

Runs the provider prevented from finishing (rate limit, timeout) are **not evaluated**: they are excluded from every
rate and counted on their own, because a quota problem says nothing about the model. Cases labelled with accepted
alternative categories are listed under "Data quality", since their label is a judgement call. Counts are runs, not
percentages; with a handful of runs per class one run moves a rate a lot, so no statistical claim is made.
"""

from __future__ import annotations

import argparse
import json
import pathlib
import statistics
from collections import defaultdict
from typing import Any

SMALL_SAMPLE = 30


def case_class(case_id: str) -> str:
    for prefix, name in (("control", "control"), ("caller", "caller"), ("adv", "adversarial")):
        if case_id.startswith(prefix):
            return name
    return "other"


def _is_accepted(run: dict[str, Any]) -> bool:
    return run.get("workflow_status") == "accepted"


def not_evaluated(run: dict[str, Any]) -> bool:
    """The provider prevented the run from finishing (quota, timeout, outage): no statement about the model."""
    return run.get("workflow_status") == "failed" and str(run.get("error_category") or "").startswith("llm_")


def _accepted_categories(run: dict[str, Any]) -> list[Any]:
    expected = run.get("expected") or {}
    return [expected.get("root_cause_category"), *expected.get("alternative_categories", [])]


def _correct(run: dict[str, Any]) -> bool:
    expected = run.get("expected") or {}
    return not expected.get("inconclusive") and run.get("predicted_category") in [
        c for c in _accepted_categories(run) if c is not None
    ]


def _false_acceptance(run: dict[str, Any]) -> bool:
    expected = run.get("expected") or {}
    return _is_accepted(run) and (bool(expected.get("inconclusive")) or not _correct(run))


def _fully_grounded(run: dict[str, Any]) -> bool:
    return (run.get("observed_quotes") or 0) > 0 and run.get("grounded_quotes") == run.get("observed_quotes")


def _false_rejection(run: dict[str, Any]) -> bool:
    """A correct, fully grounded diagnosis of a conclusive case, with a patch, that the gate did not accept."""
    return not _is_accepted(run) and _correct(run) and _fully_grounded(run) and bool(run.get("patch"))


def _same_file(a: str, b: str) -> bool:
    return a == b or a.endswith("/" + b) or b.endswith("/" + a)


def _targets_wrong_file(run: dict[str, Any]) -> bool:
    relevant = (run.get("expected") or {}).get("relevant_files") or []
    files = run.get("affected_files") or []
    return bool(relevant) and bool(files) and not any(_same_file(f, r) for f in files for r in relevant)


def _unsafe_acceptance(run: dict[str, Any]) -> bool:
    """Accepted, with a patch, while the diagnosis is wrong/unsupported or points at the wrong file."""
    return _is_accepted(run) and bool(run.get("patch")) and (_false_acceptance(run) or _targets_wrong_file(run))


def _tokens(run: dict[str, Any]) -> int:
    attempts = run.get("attempts") or []
    return sum((a.get("input_tokens") or 0) + (a.get("output_tokens") or 0) for a in attempts)


def _rate(part: int, whole: int) -> str:
    return f"{part}/{whole}" if whole else "n/a"


def per_case(report: dict[str, Any]) -> dict[str, dict[str, Any]]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for run in report["results"]:
        grouped[run["case_id"]].append(run)
    out: dict[str, dict[str, Any]] = {}
    for case_id, all_runs in grouped.items():
        runs = [r for r in all_runs if not not_evaluated(r)]
        outcomes = {(_is_accepted(r), bool(r.get("patch")), r.get("predicted_category")) for r in runs}
        expected = all_runs[0].get("expected") or {}
        out[case_id] = {
            "class": case_class(case_id),
            "runs": len(runs),
            "not_evaluated": len(all_runs) - len(runs),
            "accepted": sum(_is_accepted(r) for r in runs),
            "correct": sum(_correct(r) for r in runs),
            "patch_kept": sum(bool(r.get("patch")) for r in runs),
            "false_accepted": sum(_false_acceptance(r) for r in runs),
            "false_rejected": sum(_false_rejection(r) for r in runs),
            "unsafe_accepted": sum(_unsafe_acceptance(r) for r in runs),
            "observed_quotes": sum(r.get("observed_quotes") or 0 for r in runs),
            "grounded_quotes": sum(r.get("grounded_quotes") or 0 for r in runs),
            "attempts": sum(len(r.get("attempts") or []) for r in runs),
            "stable": len(outcomes) <= 1,
            "latency_ms": statistics.fmean(r.get("wall_ms") or 0 for r in runs) if runs else 0.0,
            "tokens": statistics.fmean(_tokens(r) for r in runs) if runs else 0.0,
            "alternatives": list(expected.get("alternative_categories") or []),
            "note": expected.get("note") or "",
        }
    return out


_SUMMED = (
    "runs",
    "not_evaluated",
    "accepted",
    "correct",
    "patch_kept",
    "false_accepted",
    "false_rejected",
    "unsafe_accepted",
    "observed_quotes",
    "grounded_quotes",
    "attempts",
)


def per_class(cases: dict[str, dict[str, Any]]) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for name in sorted({c["class"] for c in cases.values()}):
        rows = [c for c in cases.values() if c["class"] == name]
        runs = sum(c["runs"] for c in rows)
        out[name] = {
            "cases": len(rows),
            **{k: sum(c[k] for c in rows) for k in _SUMMED},
            "stable": sum(c["stable"] for c in rows),
            "latency_ms": sum(c["latency_ms"] * c["runs"] for c in rows) / runs if runs else 0.0,
            "tokens": sum(c["tokens"] * c["runs"] for c in rows) / runs if runs else 0.0,
        }
    return out


def changes(base: dict[str, dict[str, Any]], cand: dict[str, dict[str, Any]]) -> list[str]:
    lines = []
    for case_id in sorted(set(base) & set(cand)):
        b, c = base[case_id], cand[case_id]
        if not b["runs"] or not c["runs"]:
            continue
        diffs = []
        for key, label in (
            ("accepted", "accepted"),
            ("correct", "correct category"),
            ("patch_kept", "patch kept"),
            ("unsafe_accepted", "unsafe accepted"),
        ):
            if b[key] / b["runs"] != c[key] / c["runs"]:
                diffs.append(f"{label} {b[key]}/{b['runs']} -> {c[key]}/{c['runs']}")
        if diffs:
            lines.append(f"- `{case_id}` ({c['class']}): " + "; ".join(diffs))
    return lines


def _header(candidate: dict[str, Any], source: str, total_runs: int, skipped: int) -> list[str]:
    meta = candidate["run_metadata"]
    cost = (candidate.get("metrics") or {}).get("estimated_cost_usd")
    lines = [
        f"# Reliability report - {candidate['dataset_version']}",
        "",
        f"- Run: `{source}`, started {candidate['started_at']}, mode `{candidate['mode']}`",
        f"- Model `{meta.get('model')}`, prompt `{meta.get('prompt_version')}`, "
        f"quality gate `{meta.get('evaluator_version')}`, retrieval `{meta.get('retrieval_strategy')}`",
        f"- Evaluated runs: {total_runs}; **not evaluated** (provider quota/timeout, in no rate): {skipped}",
        f"- Estimated cost: {f'${cost}' if cost is not None else 'n/a (no prices configured: LLM_COST_*_PER_MTOK)'}",
    ]
    if total_runs < SMALL_SAMPLE:
        lines.append(f"- **Small sample ({total_runs} evaluated runs): read these as counts, not as rates.**")
    return lines


def _class_table(classes: dict[str, dict[str, Any]]) -> list[str]:
    out = [
        "",
        "## By case class",
        "",
        "| class | cases | runs | accepted | correct category | patch kept | false accepted | false rejected "
        "| unsafe accepted | evidence verified | mean attempts | stable cases | mean latency ms | mean tokens |",
        "|---|---|---|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    for name, c in classes.items():
        r = c["runs"]
        out.append(
            f"| {name} | {c['cases']} | {r} | {_rate(c['accepted'], r)} | {_rate(c['correct'], r)} "
            f"| {_rate(c['patch_kept'], r)} | {_rate(c['false_accepted'], r)} | {_rate(c['false_rejected'], r)} "
            f"| {_rate(c['unsafe_accepted'], r)} | {_rate(c['grounded_quotes'], c['observed_quotes'])} "
            f"| {c['attempts'] / r if r else 0:.1f} | {c['stable']}/{c['cases']} "
            f"| {c['latency_ms']:.0f} | {c['tokens']:.0f} |"
        )
    return out


def _case_table(cases: dict[str, dict[str, Any]]) -> list[str]:
    out = [
        "",
        "## By case",
        "",
        "| case | class | runs | not evaluated | accepted | correct | patch kept | unsafe accepted | stable |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    for case_id, c in sorted(cases.items()):
        out.append(
            f"| {case_id} | {c['class']} | {c['runs']} | {c['not_evaluated']} | {c['accepted']} | {c['correct']} "
            f"| {c['patch_kept']} | {c['unsafe_accepted']} | {'yes' if c['stable'] else 'no'} |"
        )
    return out


def _data_quality(cases: dict[str, dict[str, Any]]) -> list[str]:
    out: list[str] = []
    for case_id, c in sorted(cases.items()):
        if c["alternatives"]:
            out.append(
                f"- `{case_id}`: ambiguous label, also accepted: {', '.join(c['alternatives'])}. {c['note']}".rstrip()
            )
        if c["not_evaluated"]:
            out.append(f"- `{case_id}`: {c['not_evaluated']} run(s) not evaluated (provider prevented completion).")
    return ["", "## Data quality", "", *out] if out else []


def render(candidate: dict[str, Any], baseline: dict[str, Any] | None, source: str) -> str:
    cases = per_case(candidate)
    total_runs = sum(c["runs"] for c in cases.values())
    skipped = sum(c["not_evaluated"] for c in cases.values())
    out = _header(candidate, source, total_runs, skipped)
    out += _class_table(per_class(cases)) + _case_table(cases) + _data_quality(cases)
    if baseline is not None:
        bmeta = baseline["run_metadata"]
        out += [
            "",
            f"## Change against baseline (`{bmeta.get('prompt_version')}` / `{bmeta.get('evaluator_version')}`)",
            "",
        ]
        if baseline["dataset_version"] != candidate["dataset_version"]:
            out.append(
                f"Datasets differ (`{baseline['dataset_version']}` vs `{candidate['dataset_version']}`) - not compared."
            )
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
