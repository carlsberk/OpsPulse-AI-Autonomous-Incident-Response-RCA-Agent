"""Deterministic guidance for one incident: what state it is in, why, and what a person can do next.

No LLM is involved, so this works when the provider is rate-limited or unconfigured, costs nothing, and is
exactly reproducible. Every sentence is derived from stored fields (status, error category, gate checks, attempt
trace, patch) and from the operating rules in docs/RUNBOOK.md. It never claims a fix is correct.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime
from typing import Any

from src.agent.patching import PatchError, parse_unified_diff
from src.services.category_hints import external_response_hint

# gate check -> (what it verifies, what to do when it fails)
CHECK_ADVICE: dict[str, tuple[str, str]] = {
    "schema": (
        "the model's reply has the required structure",
        "Nothing to fix in the code; the model returned an incomplete or malformed analysis. A retry or a larger "
        "model usually helps; check `LLM_MAX_OUTPUT_TOKENS` if attempts were cut off.",
    ),
    "trigger_grounding": (
        "the analysis names the real crashing file and line",
        "Do not trust this diagnosis's location; compare it with the first application frame of the stack trace.",
    ),
    "evidence_grounding": (
        "every quote the model labelled 'observed' occurs verbatim in the trace, source or history",
        "At least one quote was not found in the data - treat the diagnosis as unverified and re-read the "
        "source yourself.",
    ),
    "affected_files_grounding": (
        "listed files appear in the trace or are the retrieved file",
        "The model named a file that was never in the data; ignore that file.",
    ),
    "diff_wellformed": (
        "the patch is a parseable single-file diff for the affected file",
        "The patch cannot be used as given; write the change by hand from the explanation.",
    ),
    "diff_applies": (
        "the patch applies to the retrieved source",
        "The model's context lines do not match the real file. The diagnosis may still be right - apply the idea "
        "by hand, or check whether the code changed since the analysis.",
    ),
    "patch_minimal": (
        "the patch stays within the changed-line limit",
        "The patch is larger than policy allows; ask for a smaller change or split it manually.",
    ),
    "patch_locality": (
        "the patch is near the failing line",
        "A change far from the failing line is suspicious; check that it addresses this crash.",
    ),
    "trace_code_consistency": (
        "the key or attribute named by the error appears on the retrieved failing line",
        "The error and the repository code disagree - the running version may differ from the default branch "
        "(deploy drift). Check which commit is deployed before trusting any diagnosis.",
    ),
    "fix_location": (
        "a patch to the failing function is not combined with a diagnosis that rests on caller code",
        "The evidence points at a caller, but the patch edits the callee. The fix probably belongs in the caller; "
        "look at the caller lines quoted in the evidence.",
    ),
    "self_assessment": (
        "the model's own confidence (uncalibrated, low weight)",
        "Low model confidence is a hint to look closer, not a measurement.",
    ),
    "patch_effective": (
        "the patch changes more than whitespace",
        "The proposed change does nothing; ignore the patch.",
    ),
}

# status -> (headline, explanation, state)
STATUS_GUIDE: dict[str, tuple[str, str, str]] = {
    "queued": (
        "Waiting for a worker",
        "The incident is accepted and queued; nothing has been analysed yet.",
        "waiting",
    ),
    "processing": ("Analysis in progress", "A worker has claimed the incident and is analysing it.", "waiting"),
    "failed": ("No usable analysis", "The pipeline could not produce an analysis for this incident.", "blocked"),
    "needs_review": (
        "Needs a human",
        "An analysis exists but the quality gate did not accept it, so no pull request is proposed.",
        "attention",
    ),
    "analysis_ready": (
        "Accepted, no pull request",
        "The gate accepted the analysis; no remediation was proposed.",
        "ok",
    ),
    "awaiting_approval": (
        "A patch waits for your decision",
        "The gate accepted the analysis and a draft pull request proposal waits for a reviewer.",
        "attention",
    ),
    "remediation_rejected": ("Proposal rejected", "A reviewer rejected the proposed patch; nothing was changed.", "ok"),
    "pr_created": ("Draft pull request opened", "An approved patch became a draft pull request.", "ok"),
    "pr_skipped_duplicate": ("Pull request already exists", "An open pull request for this failure exists.", "ok"),
    "pr_failed": ("Pull request could not be created", "The approved patch did not become a pull request.", "blocked"),
}

# error_category -> (why, [(kind, audience, text)])
CATEGORY_GUIDE: dict[str, tuple[str, list[tuple[str, str, str]]]] = {
    "llm_rate_limited": (
        "The LLM provider's rate or token limit was hit.",
        [
            (
                "wait",
                "operator",
                "Transient errors are re-queued with backoff automatically; if the quota is a daily "
                "limit, wait for it to reset or use another model/tier, then use Retry.",
            )
        ],
    ),
    "llm_timeout": (
        "The LLM provider timed out.",
        [("wait", "operator", "It is retried automatically; use Retry if it ended as failed.")],
    ),
    "llm_unavailable": (
        "The LLM provider or model was unavailable.",
        [
            (
                "check",
                "operator",
                "If the reason says the model is unavailable, set `MODEL_NAME` to a served model and "
                "evaluate it before switching.",
            )
        ],
    ),
    "llm_request_too_large": (
        "A single request exceeds a per-minute token cap of the provider tier.",
        [
            (
                "action",
                "operator",
                "Waiting does not help: choose another model or tier, or lower `LLM_MAX_OUTPUT_TOKENS`.",
            )
        ],
    ),
    "llm_auth": (
        "The LLM provider rejected the API key.",
        [("action", "operator", "Rotate `GROQ_API_KEY` and restart, then use Retry.")],
    ),
    "llm_not_configured": (
        "No LLM API key is configured.",
        [("action", "operator", "Set `GROQ_API_KEY`, restart, then use Retry.")],
    ),
    "llm_bad_request": (
        "The LLM provider rejected the request.",
        [
            (
                "check",
                "developer",
                "This is usually a prompt or parameter problem; check the logs for the provider code.",
            )
        ],
    ),
    "database_unavailable": (
        "The database was unavailable.",
        [("action", "operator", "Restore the database, then use Retry.")],
    ),
    "internal_error": (
        "An unexpected internal error occurred.",
        [("check", "operator", "Look for `worker iteration failed` in the JSON logs, then use Retry.")],
    ),
    "interrupted": (
        "Workers stopped mid-analysis repeatedly.",
        [("check", "operator", "Check container exits and memory limits (OOM kills), then use Retry.")],
    ),
    "retry_budget_exhausted": (
        "The model could not produce an analysis that passes the gate within the attempt budget.",
        [
            (
                "check",
                "reviewer",
                "Read the evidence and the gate checks that kept failing; the diagnosis may still be "
                "useful even though the patch was not accepted.",
            )
        ],
    ),
    "time_budget_exhausted": (
        "The language model was too slow: another attempt would have exceeded the per-incident time budget.",
        [
            (
                "action",
                "operator",
                "Check the provider's status and latency, then use Retry; raise MAX_INCIDENT_SECONDS only if "
                "slow-but-successful attempts are normal for your provider.",
            )
        ],
    ),
    "insufficient_evidence": (
        "The model reported that the data does not support a diagnosis (or the trace is too thin).",
        [("action", "developer", "Add a full stack trace or the failing source and re-submit; do not act on a guess.")],
    ),
    "no_code_fix": (
        "The analysis is grounded but no code change is appropriate - the cause looks operational.",
        [("check", "operator", "Check configuration, dependencies and downstream services; see the suggested tests.")],
    ),
    "source_unavailable": (
        "The source file could not be retrieved, so nothing could be grounded.",
        [
            (
                "check",
                "operator",
                "Check the GitHub token's access, the repository allow-list and whether the trace path "
                "maps to a file in the default branch; then re-submit.",
            )
        ],
    ),
    "malformed_model_output": (
        "The model repeatedly returned output that did not match the required structure.",
        [("action", "operator", "Re-submit later or try another model; compare the model on the evaluation set.")],
    ),
    "remediation_policy_violation": (
        "The patch violates the remediation policy (for example it touches more than the affected file).",
        [
            ("check", "reviewer", "Apply the idea by hand if it is sound; the policy blocks automatic proposals."),
            (
                "action",
                "reporter",
                "If the reason says the patch does not apply to the default branch head, the branch moved after the "
                "analysed commit: re-submit the incident with the current deployed `commit_sha` for a fresh analysis.",
            ),
        ],
    ),
    "retrieval_failed": (
        "Looking up similar past incidents failed, so the analysis ran without history.",
        [
            (
                "check",
                "operator",
                "The database may have been unavailable during the lookup; the diagnosis itself "
                "does not depend on history, but check database health in the logs.",
            )
        ],
    ),
    "remediation_skipped": (
        "Remediation is disabled or no GitHub token is configured.",
        [
            (
                "action",
                "operator",
                "Set `ENABLE_GITHUB_REMEDIATION=true` and a `GITHUB_TOKEN` to propose pull "
                "requests, or apply the patch yourself.",
            )
        ],
    ),
    "approval_rejected": ("A reviewer rejected the proposal.", []),
    "approval_expired": (
        "The approval request expired before a decision.",
        [("action", "reviewer", "Re-run the incident to get a fresh proposal.")],
    ),
    "github_permission": (
        "GitHub refused the operation.",
        [("action", "operator", "The token needs Contents and Pull requests read/write on this repository.")],
    ),
    "github_rate_limited": (
        "GitHub's rate limit was hit.",
        [
            (
                "wait",
                "operator",
                "Wait and ask a reviewer to submit the decision again; it is not retried automatically.",
            )
        ],
    ),
    "github_unavailable": ("GitHub could not be reached.", [("wait", "operator", "Retry the decision later.")]),
}


def _parse_time(value: Any) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


# A "guard or default" patch makes the failing line stop failing without saying why the value was bad. It can be
# the right fix, but it is also the classic way to hide an upstream problem, so reviewers are told when they see one.
_GUARD_LINE = re.compile(
    r"^\s*(if\b.*:|elif\b.*:|else\s*:|return\b.*|continue|pass|raise\b.*|"
    r"[\w.]+\s*=\s*(0|0\.0|\[\]|\{\}|''|\"\"|None|False|True))\s*$"
)
_DEFAULTING = re.compile(
    r"\.get\([^()]*,[^()]*\)|getattr\([^()]*,[^()]*,[^()]*\)|\bor\s+(\[\]|\{\}|0|''|\"\"|None)\b|"
    r"\bif\b.+\belse\b|\bor\s+(\[\]|\{\}|0|''|\"\")"
)


def classify_patch(added: list[str], removed: list[str]) -> str | None:
    """`guard_or_default` when the patch only adds an early return / guard / default value, else None."""
    # A line that is removed and re-added unchanged is a diff artefact (the model re-typed it), not a change.
    unchanged = {a.strip() for a in added} & {r.strip() for r in removed}
    added = [a for a in added if a.strip() not in unchanged]
    removed = [r for r in removed if r.strip() not in unchanged]
    code = [a for a in added if a.strip() and not a.strip().startswith("#")]
    if not code:
        return None
    if not removed and all(_GUARD_LINE.match(a) for a in code) and any(a.lstrip().startswith("if") for a in code):
        return "guard_or_default"
    if (
        removed
        and len(code) <= 2
        and any(_DEFAULTING.search(a) for a in code)
        and not any(_DEFAULTING.search(r) for r in removed)
    ):
        return "guard_or_default"
    return None


def patch_facts(patch: str | None) -> dict[str, Any] | None:
    """Counts only - what the patch changes, derived without a model."""
    if not patch:
        return None
    try:
        parsed = parse_unified_diff(patch)
    except PatchError:
        return {"parsable": False}
    added_lines = [text for h in parsed.hunks for op, text in h.ops if op == "+"]
    removed_lines = [text for h in parsed.hunks for op, text in h.ops if op == "-"]
    return {
        "parsable": True,
        "file": parsed.path,
        "hunks": len(parsed.hunks),
        "added": len(added_lines),
        "removed": len(removed_lines),
        "starts": [h.old_start for h in parsed.hunks if h.old_start],
        "pattern": classify_patch(added_lines, removed_lines),
    }


def failed_checks(analysis: dict[str, Any]) -> list[dict[str, Any]]:
    checks = (analysis.get("evaluation") or {}).get("checks") or {}
    rows = []
    for name, check in checks.items():
        if not isinstance(check, dict) or not check.get("max"):
            continue
        fraction = (check.get("score") or 0) / check["max"]
        if fraction >= 1 - 1e-9:
            continue
        meaning, advice = CHECK_ADVICE.get(name, ("a gate check", "See the check's detail."))
        rows.append(
            {
                "name": name,
                "blocking": bool(check.get("blocking")),
                "fraction": round(fraction, 2),
                "meaning": meaning,
                "advice": advice,
                "detail": str(check.get("detail") or "")[:400],
            }
        )
    rows.sort(key=lambda r: (not r["blocking"], r["fraction"], r["name"]))
    return rows


def build_guidance(incident: dict[str, Any], now: datetime | None = None) -> dict[str, Any]:
    now = now or datetime.now(UTC)
    status = str(incident.get("status") or "")
    category = incident.get("error_category")
    analysis = incident.get("analysis") or {}
    attempts = [a for a in analysis.get("attempts") or [] if isinstance(a, dict)]
    headline, explanation, state = STATUS_GUIDE.get(status, ("Unknown status", f"Status is {status!r}.", "attention"))
    steps: list[dict[str, str]] = []

    def add(kind: str, audience: str, text: str) -> None:
        steps.append({"kind": kind, "audience": audience, "text": text})

    why, category_steps = CATEGORY_GUIDE.get(str(category), ("", []))
    if why:
        explanation = f"{explanation} {why}"
    for kind, audience, text in category_steps:
        add(kind, audience, text)

    available_at = _parse_time(incident.get("available_at"))
    if status == "queued" and available_at and available_at > now:
        add(
            "wait",
            "operator",
            f"Deferred after a provider error; it runs again automatically at {available_at:%H:%M} UTC.",
        )
    elif status == "queued":
        add("check", "operator", "If it stays queued, check that a worker is running (`docker compose logs worker`).")
    if status == "failed":
        add("action", "reviewer", "Use *Retry analysis* once the cause above is resolved.")
    if status == "awaiting_approval":
        add(
            "action",
            "reviewer",
            "Read the evidence quotes and the patch, then approve (opens a draft PR) or reject. "
            "The approval is bound to this exact patch; you cannot decide on incidents you submitted.",
        )
    if status == "pr_created":
        add("action", "reviewer", "Review the draft pull request and let your CI run; nothing is merged automatically.")
    if status == "analysis_ready" and category != "remediation_skipped":
        add("check", "reviewer", "Review the diagnosis and apply the change through your normal process.")
    if status == "pr_failed":
        add(
            "check",
            "operator",
            "See the runbook section 'PR creation fails' (existing branch, stale patch, token scope).",
        )

    fix_location = ((analysis.get("evaluation") or {}).get("checks") or {}).get("fix_location") or {}
    if incident.get("suggested_patch") and (fix_location.get("score") or 0) < (fix_location.get("max") or 0):
        add(
            "check",
            "reviewer",
            "The patch changes the failing function, but the evidence quotes code from a caller (gate check "
            "fix_location). Check where the bad value comes from before accepting a fix here: if a caller creates "
            "or passes it, the real fix belongs there. (This is a heuristic; it also fires when the model quotes a "
            "caller only to rule it out.)",
        )
    hint = external_response_hint(analysis)
    if hint:
        add("check", "reviewer", hint)
    patch = patch_facts(incident.get("suggested_patch"))
    if patch and patch.get("pattern") == "guard_or_default":
        add(
            "check",
            "reviewer",
            "The patch only adds a guard or default value in the failing function. That can be right, but it can also "
            "hide the real cause (a caller passing a bad value, or missing data upstream): check where the value "
            "comes from before accepting it.",
        )

    checks = failed_checks(analysis)
    blocking = [c for c in checks if c["blocking"]]
    if status == "needs_review" and blocking:
        top = blocking[0]
        add("check", "reviewer", f"Most important failed check: **{top['name']}** - {top['advice']}")
    if status in ("needs_review", "analysis_ready", "awaiting_approval") and analysis.get("uncertainties"):
        add(
            "check", "reviewer", "Read the model's own uncertainties before acting: they list what it could not verify."
        )
    if analysis.get("tests_to_run"):
        add("check", "developer", "Run the suggested tests yourself - none were executed by the service.")

    facts: list[str] = []
    if attempts:
        tokens = sum((a.get("input_tokens") or 0) + (a.get("output_tokens") or 0) for a in attempts)
        facts.append(f"{len(attempts)} attempt(s), {tokens:,} provider tokens")
        if any(a.get("truncated") for a in attempts):
            add(
                "action",
                "operator",
                "Replies were cut at the output-token limit; raise `LLM_MAX_OUTPUT_TOKENS` if the "
                "provider tier allows it.",
            )
        failing = [set(a.get("failed_checks") or []) - {"self_assessment"} for a in attempts if "gate_passed" in a]
        if len(failing) > 1 and (common := set.intersection(*failing)):
            facts.append("the same check(s) failed in every attempt: " + ", ".join(sorted(common)))
    if incident.get("quality_score") is not None:
        facts.append(f"gate score {float(incident['quality_score']):.2f} (a rubric score, not a probability)")
    if patch and patch.get("parsable"):
        facts.append(
            f"patch: {patch['file']}, +{patch['added']} -{patch['removed']} in {patch['hunks']} hunk(s), not run"
        )

    return {
        "state": state,
        "headline": headline,
        "explanation": explanation,
        "next_steps": steps,
        "failed_checks": checks,
        "facts": facts,
        "patch": patch,
    }
