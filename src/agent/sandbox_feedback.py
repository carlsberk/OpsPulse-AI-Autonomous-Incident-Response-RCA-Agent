"""Turn a failed sandbox verdict into feedback for one more analysis pass (opt-in: SANDBOX_FEEDBACK_ATTEMPTS).

The quality gate checks a patch against the source text; the sandbox runs the repository's tests. When the tests
still fail after the patch (or the patch broke a passing test), the model gets that evidence once, through the same
`<PREVIOUS_FEEDBACK>` channel the gate uses. Pure functions only: the service owns the sandbox runs and side effects.
"""

from __future__ import annotations

from typing import Any

# Verdicts a second attempt can improve on. `timeout`, `error` and `unavailable` say nothing about the patch.
RETRYABLE = frozenset({"still_failing", "regression"})

# Higher is better. `partly_fixed` and `no_regression` are not retried, but a repaired patch is compared against them.
_RANK = {
    "regression": 0,
    "still_failing": 1,
    "partly_fixed": 2,
    "no_regression": 3,
    "fixes_failing_tests": 4,
}
MAX_OUTPUT_CHARS = 2500
MAX_TEST_IDS = 10


def needs_repair(report: dict[str, Any]) -> bool:
    return report.get("status") in RETRYABLE


def is_improvement(old: dict[str, Any], new: dict[str, Any]) -> bool:
    """A repaired patch replaces the first one only if its verdict is strictly better."""
    return _RANK.get(new.get("status"), -1) > _RANK.get(old.get("status"), -1)  # type: ignore[arg-type]


def build_feedback(report: dict[str, Any]) -> str:
    """Feedback text from a sandbox report dict (`SandboxReport.as_dict()`): verdict, failing tests, output tail."""
    after = report.get("after") or {}
    failed = list(after.get("failed") or [])[:MAX_TEST_IDS]
    lines = [
        "The patch you proposed was applied and the repository's tests were run in a sandbox. "
        f"Verdict: {report.get('status')} - {report.get('summary')}.",
    ]
    if failed:
        lines.append("Tests failing after your patch: " + ", ".join(failed))
    tail = str(after.get("output_tail") or "").strip()[-MAX_OUTPUT_CHARS:]
    if tail:
        lines += ["Test output after your patch (tail):", tail]
    lines.append(
        "Propose a corrected patch for the same root cause that makes these tests pass without breaking others, "
        "or keep the analysis and say so if the patch was right and the tests are unrelated."
    )
    return "\n".join(lines)
