"""Metric definitions. Every metric is a ratio with an explicit numerator and denominator; a metric whose
denominator is 0 is reported as None (not 0, not 1).

All metrics here are deterministic: they compare recorded facts (labels, verified quotes, statuses). No LLM
judge is used. Category labels were assigned by the dataset author, so "accuracy" means agreement with those
labels, not ground truth about real systems.
"""

from __future__ import annotations

import math
from collections.abc import Callable
from itertools import pairwise
from typing import Any

from src.agent.patching import path_matches

Result = dict[str, Any]


def _ratio(num: int | float, den: int | float, definition: str) -> dict[str, Any]:
    return {
        "value": round(num / den, 4) if den else None,
        "numerator": num,
        "denominator": den,
        "definition": definition,
    }


def _provider_failed(r: Result) -> bool:
    return r["workflow_status"] == "failed" and str(r.get("error_category") or "").startswith("llm_")


def _abstained(r: Result) -> bool:
    return r["declared_insufficient"] or r["predicted_category"] == "unknown"


def category_matches(r: Result) -> bool:
    """The predicted category is the labelled one or an accepted alternative for an ambiguous label."""
    expected = r["expected"]
    return r["predicted_category"] == expected["root_cause_category"] or r["predicted_category"] in expected.get(
        "alternative_categories", []
    )


def _correct(r: Result) -> bool:
    """Outcome used for calibration: a conclusive case diagnosed with the labelled category."""
    return not r["expected"]["inconclusive"] and category_matches(r)


def _unsupported(r: Result) -> bool:
    """The analysis lacked sufficient support: an inconclusive-labelled case, or an unverifiable quote."""
    return r["expected"]["inconclusive"] or r["grounded_quotes"] < r["observed_quotes"]


def calibration(
    results: list[Result], key: str, bins: tuple[float, ...] = (0.0, 0.5, 0.7, 0.85, 1.0001)
) -> dict[str, Any]:
    """Brier score and per-bin accuracy of a 0..1 score against `_correct`. Small samples: indicative only."""
    pairs = [(float(r[key]), _correct(r)) for r in results if isinstance(r.get(key), (int, float))]
    if not pairs:
        return {"samples": 0, "brier": None, "bins": []}
    brier = sum((p - (1.0 if y else 0.0)) ** 2 for p, y in pairs) / len(pairs)
    table = []
    for low, high in pairwise(bins):
        members = [(p, y) for p, y in pairs if low <= p < high]
        if members:
            table.append(
                {
                    "range": f"{low:.2f}-{min(high, 1.0):.2f}",
                    "n": len(members),
                    "mean_score": round(sum(p for p, _ in members) / len(members), 3),
                    "accuracy": round(sum(1 for _, y in members if y) / len(members), 3),
                }
            )
    return {"samples": len(pairs), "brier": round(brier, 4), "bins": table}


def _percentile(values: list[int], q: float) -> int | None:
    if not values:
        return None
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, math.ceil(q * len(ordered)) - 1)]


def consistency(results: list[Result]) -> dict[str, Any] | None:
    """Run-to-run variation, reported only when a case was run more than once (`--repeat`).

    A single live run of a stochastic model is one sample: two sessions of the same case can end differently. This
    makes that visible - per case, how many runs were accepted and how many had the labelled category - and counts
    the cases whose outcome never changed.
    """
    by_case: dict[str, list[Result]] = {}
    for r in results:
        if "case_id" in r and not _provider_failed(r):
            by_case.setdefault(r["case_id"], []).append(r)
    repeated = {k: v for k, v in by_case.items() if len(v) > 1}
    if not repeated:
        return None
    per_case = {}
    stable = 0
    for case_id, runs in repeated.items():
        outcomes = {(r["workflow_status"], r["predicted_category"]) for r in runs}
        stable += len(outcomes) == 1
        per_case[case_id] = {
            "runs": len(runs),
            "accepted": sum(bool(r["gate_passed"]) for r in runs),
            "correct_category": sum(_correct(r) for r in runs),
            "distinct_outcomes": len(outcomes),
        }
    return {
        "cases_repeated": len(repeated),
        "stable_outcome_rate": _ratio(
            stable, len(repeated), "repeated cases whose outcome never changed / repeated cases"
        ),
        "per_case": per_case,
    }


def compute_metrics(
    results: list[Result], cost_in_per_mtok: float = 0.0, cost_out_per_mtok: float = 0.0
) -> dict[str, Any]:
    attempts = [a for r in results for a in r["attempts"]]
    model_attempts = [a for a in attempts if "schema_valid" in a]  # excludes provider errors
    # Cases the provider prevented from finishing (rate limit, auth, outage) say nothing about analysis quality:
    # they are excluded from the quality metrics and reported separately.
    evaluated = [r for r in results if not _provider_failed(r)]
    conclusive = [r for r in evaluated if not r["expected"]["inconclusive"]]
    inconclusive = [r for r in evaluated if r["expected"]["inconclusive"]]
    accepted = [r for r in evaluated if r["gate_passed"]]
    with_files = [r for r in evaluated if r["expected"]["relevant_files"]]
    observed = sum(r["observed_quotes"] for r in evaluated)
    grounded = sum(r["grounded_quotes"] for r in evaluated)
    files = sum(len(r["affected_files"]) for r in evaluated)
    grounded_files = sum(r["grounded_files"] for r in evaluated)

    def wrong(r: Result) -> bool:
        return r["expected"]["inconclusive"] or not category_matches(r)

    def hit(r: Result) -> bool:
        return any(path_matches(p, e) for p in r["affected_files"] for e in r["expected"]["relevant_files"])

    count: Callable[[list[Result], Callable[[Result], bool]], int] = lambda rs, f: sum(1 for r in rs if f(r))  # noqa: E731
    latencies = [a["latency_ms"] for a in model_attempts if a.get("latency_ms") is not None]
    tokens_in = [a["input_tokens"] for a in model_attempts if a.get("input_tokens") is not None]
    tokens_out = [a["output_tokens"] for a in model_attempts if a.get("output_tokens") is not None]
    cost = None
    if tokens_in and (cost_in_per_mtok or cost_out_per_mtok):
        cost = round(sum(tokens_in) / 1e6 * cost_in_per_mtok + sum(tokens_out) / 1e6 * cost_out_per_mtok, 6)

    return {
        "cases": len(results),
        "cases_not_evaluated": [r["case_id"] for r in results if _provider_failed(r)],
        "structured_output_validity": _ratio(
            count(model_attempts, lambda a: a["schema_valid"]) if model_attempts else 0,
            len(model_attempts),
            "LLM attempts whose reply validated against RCAOutput / LLM attempts (provider errors excluded)",
        ),
        "category_accuracy": _ratio(
            count(conclusive, category_matches),
            len(conclusive),
            "final category == labelled category (or an accepted alternative) / cases labelled conclusive",
        ),
        "evidence_grounding_accuracy": _ratio(
            grounded,
            observed,
            "'observed' quotes found verbatim in their cited source / all 'observed' quotes (final analyses)",
        ),
        "unsupported_claim_rate": _ratio(
            (observed - grounded) + (files - grounded_files),
            observed + files,
            "(unverifiable 'observed' quotes + affected files absent from trace/retrieved file) / (all quotes + "
            "all affected files)",
        ),
        "abstention_recall": _ratio(
            count(inconclusive, _abstained),
            len(inconclusive),
            "inconclusive-labelled cases where the model declared insufficient evidence or category 'unknown' / "
            "inconclusive-labelled cases",
        ),
        "inconclusive_not_accepted_rate": _ratio(
            count(inconclusive, lambda r: not r["gate_passed"]),
            len(inconclusive),
            "inconclusive-labelled cases the quality gate did NOT accept / inconclusive-labelled cases",
        ),
        "false_abstention_rate": _ratio(
            count(conclusive, _abstained),
            len(conclusive),
            "conclusive-labelled cases where the model abstained / conclusive-labelled cases",
        ),
        "gate_acceptance_rate": _ratio(
            len(accepted), len(evaluated), "cases accepted by the quality gate / evaluated cases"
        ),
        "false_acceptance_rate": _ratio(
            count(accepted, wrong),
            len(accepted),
            "accepted cases that are inconclusive-labelled or have the wrong category / accepted cases",
        ),
        "relevant_file_hit_rate": _ratio(
            count(with_files, hit),
            len(with_files),
            "cases whose affected_files contain a labelled relevant file / cases with labelled files",
        ),
        "unsupported_acceptance_rate": _ratio(
            count([r for r in evaluated if _unsupported(r)], lambda r: r["gate_passed"]),
            len([r for r in evaluated if _unsupported(r)]),
            "unsupported analyses (inconclusive-labelled or with an unverifiable quote) the gate accepted / "
            "unsupported analyses",
        ),
        "workflow_failure_rate": _ratio(
            count(results, lambda r: r["workflow_status"] == "failed"),
            len(results),
            "cases ending in workflow status 'failed' (e.g. provider errors) / cases",
        ),
        "avg_attempts": round(len(attempts) / len(results), 3) if results else None,
        "llm_latency_ms": {
            "mean": round(sum(latencies) / len(latencies)) if latencies else None,
            "p95": _percentile(latencies, 0.95),
            "samples": len(latencies),
        },
        "case_wall_ms": {
            "mean": round(sum(r["wall_ms"] for r in results) / len(results)) if results else None,
            "p95": _percentile([r["wall_ms"] for r in results], 0.95),
        },
        "tokens": {
            "input": sum(tokens_in) if tokens_in else None,
            "output": sum(tokens_out) if tokens_out else None,
            "attempts_with_usage": len(tokens_in),
        },
        "estimated_cost_usd": cost,
        "calibration": {
            "model_confidence": calibration(evaluated, "model_confidence"),
            "quality_score": calibration(evaluated, "quality_score"),
        },
        "consistency": consistency(results),
    }
