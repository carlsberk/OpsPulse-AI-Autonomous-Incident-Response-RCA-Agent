"""One incident's run trace: versions, per-attempt gate verdicts, latency and tokens - from the stored record.

Deterministic and read-only. Nothing here is estimated: cost is reported only if prices are configured (they are not),
and a request id is present only if the analysis ran in the process that handled the submitting request.
"""

from __future__ import annotations

from typing import Any

_VERSION_KEYS = ("agent_version", "prompt_version", "evaluator_version", "retrieval_strategy", "model")


def build_trace(incident: dict[str, Any]) -> dict[str, Any]:
    analysis = incident.get("analysis") or {}
    meta = analysis.get("execution_metadata") or {}
    attempts = [a for a in analysis.get("attempts") or [] if isinstance(a, dict)]
    rows = [
        {
            "iteration": a.get("iteration"),
            "latency_ms": a.get("latency_ms"),
            "input_tokens": a.get("input_tokens"),
            "output_tokens": a.get("output_tokens"),
            "truncated": a.get("truncated"),
            "valid_json": a.get("valid_json"),
            "schema_valid": a.get("schema_valid"),
            "gate_score": a.get("gate_score"),
            "gate_passed": a.get("gate_passed"),
            "failed_checks": a.get("failed_checks") or [],
            "decision": a.get("decision"),
        }
        for a in attempts
    ]

    def total(key: str) -> int | None:
        values: list[int | float] = [v for r in rows if isinstance(v := r[key], int | float)]
        return int(sum(values)) if values else None

    return {
        "incident_id": incident.get("incident_id"),
        "request_id": analysis.get("trace_request_id"),
        "status": incident.get("status"),
        "error_category": incident.get("error_category"),
        "versions": {k: meta.get(k) for k in _VERSION_KEYS},
        "attempts": rows,
        "totals": {
            "llm_calls": len(rows),
            "latency_ms": total("latency_ms"),
            "input_tokens": total("input_tokens"),
            "output_tokens": total("output_tokens"),
            "estimated_cost_usd": None,
        },
        "final_quality_score": incident.get("quality_score"),
    }
