"""Evidence graph of one incident: claim -> evidence -> patch -> file, with an honest status on every link.

Built from the stored record only (no LLM). What can be re-checked is re-checked; what cannot is labelled:

- `verified_in_record`  observed quote found in the stored stack trace / error message
- `verified_by_gate`    observed quote from retrieved code or history; the source text is not stored, so this rests on
                        the quality gate having verified it at analysis time
- `unverified`          observed quote the gate could not find in its source (or that is too short to check)
- `not_checked`         inference / hypothesis: a deduction, never quote-checked
"""

from __future__ import annotations

import re
from typing import Any

from src.agent.patching import PatchError, parse_code_window
from src.services.category_hints import external_response_hint

_PROBLEM = re.compile(r"observed evidence #(\d+)")
_MIN_ANCHOR_CHARS = 12


def _squash(text: str) -> str:
    return " ".join(text.split())


def _gate_problems(analysis: dict[str, Any]) -> dict[int, str]:
    checks = (analysis.get("evaluation") or {}).get("checks") or {}
    detail = (checks.get("evidence_grounding") or {}).get("detail")
    problems: dict[int, str] = {}
    if isinstance(detail, str):
        for part in detail.split("; "):
            match = _PROBLEM.search(part)
            if match:
                problems[int(match.group(1))] = part
    return problems


def _diff_text(patch: str) -> str:
    """Context and removed lines of a unified diff: the code the patch is anchored on."""
    lines = []
    for raw in patch.splitlines():
        if raw.startswith(("---", "+++", "@@", "+")):
            continue
        lines.append(raw[1:] if raw[:1] in (" ", "-") else raw)
    return _squash("\n".join(lines))


def _window_text(analysis: dict[str, Any]) -> str:
    """Code lines of the stored windows (line-number gutter removed), squashed."""
    windows = analysis.get("source_windows")
    lines: list[str] = []
    for block in windows.values() if isinstance(windows, dict) else []:
        try:
            lines.extend(parse_code_window(str(block))[1])
        except PatchError:
            continue
    return _squash("\n".join(lines))


def _status(
    item: dict[str, Any], index: int, trace_text: str, problems: dict[int, str], gated: bool, window_text: str = ""
) -> str:
    """Verification status of one evidence item; `index` is its 1-based position among observed items."""
    if item.get("kind") != "observed":
        return "not_checked"
    if index in problems:
        return "unverified"
    quote = _squash(str(item.get("quote") or ""))
    if item.get("source") == "stack_trace":
        return "verified_in_record" if quote and quote in trace_text else "unverified"
    if item.get("source") == "code_context" and quote and quote in window_text:
        return "verified_in_record"  # re-checked here against the stored window
    return "verified_by_gate" if gated else "unverified"


def build_evidence_graph(incident: dict[str, Any]) -> dict[str, Any]:
    analysis = incident.get("analysis") or {}
    evaluation = analysis.get("evaluation") or {}
    checks = evaluation.get("checks") or {}
    items = [e for e in analysis.get("evidence") or [] if isinstance(e, dict)]
    trace_text = _squash(f"{incident.get('error_message') or ''}\n{incident.get('stack_trace') or ''}")
    problems = _gate_problems(analysis)
    window_text = _window_text(analysis)
    patch = incident.get("suggested_patch") or ""
    anchored = _diff_text(patch) if patch else ""
    category = analysis.get("root_cause_category")
    chain = (analysis.get("diagnostic_chain") or {}).get("primary_root_cause") or {}

    nodes: list[dict[str, Any]] = [
        {"id": "incident", "type": "incident", "label": str(incident.get("error_message") or "")[:200]},
        {
            "id": "claim",
            "type": "claim",
            "label": f"{category}: {chain.get('technical_explanation')}"[:400] if category else "no diagnosis",
        },
    ]
    edges: list[dict[str, Any]] = [{"from": "incident", "to": "claim", "relation": "diagnosed_as"}]
    flags: list[str] = []
    observed = verified = anchoring = 0
    for position, item in enumerate(items, start=1):
        node_id = f"evidence-{position}"
        is_observed = item.get("kind") == "observed"
        observed += is_observed
        status = _status(item, observed, trace_text, problems, bool(checks), window_text)
        verified += is_observed and status != "unverified"
        quote = _squash(str(item.get("quote") or ""))
        nodes.append(
            {
                "id": node_id,
                "type": "evidence",
                "kind": item.get("kind"),
                "source": item.get("source"),
                "claim": str(item.get("claim") or "")[:400],
                "quote": str(item.get("quote") or "")[:300],
                "status": status,
            }
        )
        supports = status in ("verified_in_record", "verified_by_gate")
        edges.append({"from": node_id, "to": "claim", "relation": "supports" if supports else "suggests"})
        if status == "unverified":
            flags.append(f"{node_id}: observed quote could not be verified")
        if patch and len(quote) >= _MIN_ANCHOR_CHARS and quote in anchored:
            anchoring += 1
            edges.append({"from": node_id, "to": "patch", "relation": "anchors"})

    if patch:
        nodes.append({"id": "patch", "type": "patch", "label": f"{len(patch.splitlines())} diff lines"})
        edges.append({"from": "claim", "to": "patch", "relation": "proposes"})
        for name in analysis.get("affected_files") or []:
            if isinstance(name, str):
                nodes.append({"id": f"file:{name}", "type": "file", "label": name})
                edges.append({"from": "patch", "to": f"file:{name}", "relation": "changes"})
        if verified == 0:
            flags.append("a patch is proposed but no observed evidence was verified")
        elif anchoring == 0:
            flags.append("no evidence quote touches the code the patch changes")
    if observed == 0:
        flags.append("no observed evidence: the diagnosis rests on inference only")
    hint = external_response_hint(analysis)
    if hint:
        flags.append("category: " + hint[:300])
    fix_location = checks.get("fix_location") or {}
    if fix_location.get("detail") and fix_location.get("score", 0) < fix_location.get("max", 0):
        flags.append("fix_location: " + str(fix_location["detail"])[:300])

    return {
        "incident_id": incident.get("incident_id"),
        "nodes": nodes,
        "edges": edges,
        "flags": flags,
        "summary": {
            "evidence": len(items),
            "observed": observed,
            "observed_verified": verified,
            "anchoring_patch": anchoring,
        },
    }
