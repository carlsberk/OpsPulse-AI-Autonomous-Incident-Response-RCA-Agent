"""Does showing the model an earlier reviewer's note change the patch it proposes? Live model, notes on versus off.

    python -m evals.reviewer_memory_eval --repeat 3 --out evals/results/reviewer-memory.json

Each case in `rca_reviewer_memory.json` has an earlier, similar incident whose patch a reviewer REJECTED with a note
that says how the fix should look (raise instead of defaulting, return None instead of 0.0, raise instead of
returning "").
The naive fix is the rejected one. Every case runs `--repeat` times with the reviewer notes in the prompt and `--repeat`
times without (`HISTORY_REVIEWER_NOTES`), and the added lines of the proposed patch are checked by pattern: did it do
what the note asked, did it do the rejected thing, or did it propose no patch.

The two conditions differ only in the one `human review ...` line of the history entry (the rejected patch itself is in
the prompt either way), so any difference is the effect of the reviewer's words. Small n: read counts, not rates.
"""

from __future__ import annotations

import argparse
import collections
import json
import pathlib
import re
import time
from datetime import UTC, datetime
from typing import Any

from evals.dataset import load_rca_dataset
from evals.harness import ScriptedModel, run_final
from evals.run_rca import DATASET_FILES
from src.config import settings
from src.versions import run_metadata

# case id -> (the patch does what the note asked, the patch does what the reviewer rejected); matched on added lines
CHECKS: dict[str, tuple[str, str]] = {
    "memory-config-default": (r"\braise\b", r"\.get\(\s*[\"']cache_ttl[\"']\s*,"),
    "memory-ratio-none": (r"return\s+None\b|\bNone\s+if\b|else\s+None\b", r"(?:return|else)\s+0(?:\.0)?\b"),
    "memory-lookup-raise": (r"\braise\s+(?:LookupError|KeyError|ValueError)", r"return\s+(?:\"\"|'')"),
}


def added_lines(patch: str | None) -> str:
    return "\n".join(
        line[1:] for line in (patch or "").splitlines() if line.startswith("+") and not line.startswith("+++")
    )


def classify(case_id: str, patch: str | None) -> str:
    """followed_note | did_rejected_thing | other_patch | no_patch"""
    if not patch:
        return "no_patch"
    follows, rejected = CHECKS[case_id]
    added = added_lines(patch)
    if re.search(rejected, added):
        return "did_rejected_thing"
    return "followed_note" if re.search(follows, added) else "other_patch"


def _scripted(case: Any) -> Any:
    model = ScriptedModel(case)
    return lambda temperature: model


def run(mode: str, repeat: int, sleep: float, only: list[str] | None) -> dict[str, Any]:
    cases = [c for c in load_rca_dataset(DATASET_FILES["reviewer_memory"]).cases if not only or c.id in only]
    rows: list[dict[str, Any]] = []
    for notes_on in (True, False):
        object.__setattr__(settings, "history_reviewer_notes", notes_on)
        for case in cases:
            for index in range(repeat):
                factory = _scripted(case) if mode == "mock" else None
                final, wall_ms = run_final(case, factory)
                failed = final["workflow_status"] == "failed"
                outcome = "not_evaluated" if failed else classify(case.id, final["suggested_patch"])
                rows.append(
                    {
                        "case_id": case.id,
                        "notes": "on" if notes_on else "off",
                        "run": index + 1,
                        "outcome": outcome,
                        "patch": final["suggested_patch"],
                        "wall_ms": wall_ms,
                    }
                )
                print(f"  notes={'on ' if notes_on else 'off'} {case.id} #{index + 1}: {outcome}")
                if mode == "live":
                    time.sleep(sleep)
    object.__setattr__(settings, "history_reviewer_notes", True)
    return {
        "run_at": datetime.now(UTC).isoformat(),
        "mode": mode,
        "run_metadata": run_metadata(),
        "rows": rows,
        "counts": {
            side: dict(collections.Counter(r["outcome"] for r in rows if r["notes"] == side)) for side in ("on", "off")
        },
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--mode", choices=("mock", "live"), default="live")
    parser.add_argument("--repeat", type=int, default=3)
    parser.add_argument("--sleep", type=float, default=6.0)
    parser.add_argument("--case", action="append", dest="cases")
    parser.add_argument("--out", type=pathlib.Path, required=True)
    args = parser.parse_args(argv)
    if args.mode == "live" and not settings.groq_api_key:
        raise SystemExit("live mode needs GROQ_API_KEY")
    report = run(args.mode, args.repeat, args.sleep, args.cases)
    args.out.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    print(json.dumps(report["counts"], indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
