"""Cases where an earlier reviewer's rejection says how a fix should look.

Run: python -m evals.datasets.build_rca_reviewer_memory

Each case has a failing function and a similar *earlier* incident in the same repository whose patch a reviewer
REJECTED with a note ("do not default, raise instead"). The naive fix is the rejected one.
`evals.reviewer_memory_eval` runs every case with and without the reviewer notes in the prompt and checks, by pattern,
whether the proposed patch follows the stated preference. The checks live in that script, not here: they are the
evaluation, not the case.

Mock replies follow the note, so mock mode only exercises the plumbing.
"""

from __future__ import annotations

import json
import pathlib
from typing import Any

from evals.datasets.build_rca_cases import py_trace, reply

OUT = pathlib.Path(__file__).with_name("rca_reviewer_memory.json")
REPO = "eval/memory"
CASES: list[dict[str, Any]] = []


def case(case_id, scenario, files, failing, past, expected, mock_reply) -> None:
    final_now, frame_now = failing
    final_past, frame_past, rejected_patch, note, summary = past
    CASES.append(
        {
            "id": case_id,
            "scenario": scenario,
            "repo_name": REPO,
            "error_message": final_now,
            "stack_trace": py_trace(files, [frame_now], final_now),
            "files": files,
            "history": [
                {
                    "error_message": final_past,
                    "stack_trace": py_trace(files, [frame_past], final_past),
                    "affected_file": frame_past[0],
                    "root_cause_summary": summary,
                    "status": "remediation_rejected",
                    "suggested_patch": rejected_patch,
                    "reviewer_decision": "rejected",
                    "reviewer_note": note,
                }
            ],
            "expected": {"inconclusive": False, **expected},
            "mock_replies": [mock_reply],
        }
    )


SETTINGS = """def database_url(config):
    return config["database_url"]


def cache_ttl(config):
    return int(config["cache_ttl"])
"""
case(
    "memory-config-default",
    "A missing config key; an earlier patch that defaulted the value was rejected: raise instead",
    {"app/settings.py": SETTINGS},
    ("KeyError: 'cache_ttl'", ("app/settings.py", "cache_ttl", 'int(config["cache_ttl"])')),
    (
        "KeyError: 'database_url'",
        ("app/settings.py", "database_url", 'config["database_url"]'),
        "--- a/app/settings.py\n+++ b/app/settings.py\n@@ -1,2 +1,2 @@\n def database_url(config):\n"
        '-    return config["database_url"]\n+    return config.get("database_url", "")\n',
        "Defaulting hides a misconfigured deployment. Raise a clear error that names the missing key instead.",
        "database_url reads a required key that the deployment did not set",
    ),
    {"root_cause_category": "missing_key", "relevant_files": ["app/settings.py"]},
    reply(
        "missing_key",
        quote='return int(config["cache_ttl"])',
        fix={
            "old": '    return int(config["cache_ttl"])\n',
            "new": '    if "cache_ttl" not in config:\n        raise KeyError("cache_ttl is not configured")\n'
            '    return int(config["cache_ttl"])\n',
        },
    ),
)

METRICS = """def error_rate(errors, total):
    return errors / total


def success_rate(ok, total):
    return ok / total
"""
case(
    "memory-ratio-none",
    "A division by zero; an earlier patch that returned 0.0 was rejected: return None",
    {"app/metrics.py": METRICS},
    ("ZeroDivisionError: division by zero", ("app/metrics.py", "success_rate", "return ok / total")),
    (
        "ZeroDivisionError: division by zero",
        ("app/metrics.py", "error_rate", "return errors / total"),
        "--- a/app/metrics.py\n+++ b/app/metrics.py\n@@ -1,2 +1,2 @@\n def error_rate(errors, total):\n"
        "-    return errors / total\n+    return 0.0 if total == 0 else errors / total\n",
        "0.0 reads as a perfect score. Return None when total is 0 so the dashboard shows n/a.",
        "error_rate divides by a total that is zero when there was no traffic",
    ),
    {"root_cause_category": "logic_error", "relevant_files": ["app/metrics.py"]},
    reply(
        "logic_error",
        quote="return ok / total",
        fix={"old": "    return ok / total\n", "new": "    return None if total == 0 else ok / total\n"},
    ),
)

USERS = """def find_user(users, user_id):
    return users.get(user_id)


def display_name(users, user_id):
    user = find_user(users, user_id)
    return user.name


def email_of(users, user_id):
    user = find_user(users, user_id)
    return user.email
"""
case(
    "memory-lookup-raise",
    "A missing account; an earlier patch that returned an empty string was rejected: raise LookupError",
    {"app/users.py": USERS},
    ("AttributeError: 'NoneType' object has no attribute 'email'", ("app/users.py", "email_of", "return user.email")),
    (
        "AttributeError: 'NoneType' object has no attribute 'name'",
        ("app/users.py", "display_name", "return user.name"),
        "--- a/app/users.py\n+++ b/app/users.py\n@@ -5,3 +5,5 @@\n def display_name(users, user_id):\n"
        '     user = find_user(users, user_id)\n+    if user is None:\n+        return ""\n     return user.name\n',
        'Returning "" hides a missing account. Raise LookupError(user_id) so the caller can answer 404.',
        "display_name dereferences a lookup that returns None for an unknown account",
    ),
    {"root_cause_category": "null_reference", "relevant_files": ["app/users.py"]},
    reply(
        "null_reference",
        quote="return user.email",
        fix={
            "old": "    return user.email\n",
            "new": "    if user is None:\n        raise LookupError(user_id)\n    return user.email\n",
        },
    ),
)


def main() -> None:
    OUT.write_text(json.dumps({"version": "rca-reviewer-memory-v1", "cases": CASES}, indent=2) + "\n", encoding="utf-8")
    print(f"wrote {len(CASES)} reviewer-memory cases to {OUT}")


if __name__ == "__main__":
    main()
