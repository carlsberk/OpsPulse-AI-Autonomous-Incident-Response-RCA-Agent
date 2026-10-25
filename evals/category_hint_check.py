"""How often would the category hint fire on the evaluation cases, and is it ever wrong? No model is called.

    python -m evals.category_hint_check

For every case in every dataset the check asks: "if the model had blamed our own code (category `missing_key`) and had
quoted the failing statement from the trace, would the hint appear?" The hint should appear exactly when the case's
label is `invalid_external_response` (the failing value comes from an upstream response) and for no other case. Any
other hit is a false alarm and is printed. This measures the rule against the repository's own labelled cases (small and
author-written), not against real incidents.
"""

from __future__ import annotations

import argparse
import re
import sys

from evals.dataset import load_rca_dataset
from evals.run_rca import DATASET_FILES
from src.agent.patching import format_code_window
from src.services.category_hints import external_response_hint

_FINAL_ERROR = re.compile(r"^[A-Za-z_][\w.]*(?:Error|Exception|Timeout)\b")


def failing_statement(trace: str) -> str:
    """The code line of the innermost frame (the line printed just above the final error line)."""
    lines = [line for line in trace.splitlines() if line.strip()]
    for index in range(len(lines) - 1, 0, -1):
        if _FINAL_ERROR.match(lines[index].strip()):
            candidate = lines[index - 1]
            if not candidate.lstrip().startswith("File "):
                return candidate.strip()
    return ""


def hint_for_case(case) -> str | None:
    quote = failing_statement(case.stack_trace)
    if not quote or not case.files:
        return None
    windows = {name: format_code_window(text.splitlines(), 1) for name, text in case.files.items()}
    analysis = {
        "root_cause_category": "missing_key",
        "evidence": [{"kind": "observed", "source": "code_context", "quote": quote}],
        "source_windows": windows,
    }
    return external_response_hint(analysis)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.parse_args(argv)
    total = fired = 0
    expected_hits: list[str] = []
    false_alarms: list[str] = []
    missed: list[str] = []
    for name, path in DATASET_FILES.items():
        for case in load_rca_dataset(path).cases:
            total += 1
            hint = hint_for_case(case)
            external = case.expected.root_cause_category == "invalid_external_response"
            if hint:
                fired += 1
                (expected_hits if external else false_alarms).append(f"{name}/{case.id}")
            elif external:
                missed.append(f"{name}/{case.id}")
    print(f"cases checked: {total}; hint fired on {fired}")
    print(f"  correct (label is invalid_external_response): {len(expected_hits)} {expected_hits}")
    print(f"  false alarms (label is something else):        {len(false_alarms)} {false_alarms}")
    print(f"  labelled invalid_external_response, no hint:   {len(missed)} {missed}")
    return 1 if false_alarms else 0


if __name__ == "__main__":
    sys.exit(main())
