"""Reviewer decisions on earlier incidents, as text safe to show a model.

A reviewer's note is typed by a person and can contain anything: newlines, markup, text that reads like an instruction.
It is shown to the model only as a short, single-line quotation labelled as an earlier human decision.
"""

from __future__ import annotations

import re
from typing import Any

MAX_NOTE_CHARS = 300
_CONTROL = re.compile(r"[\x00-\x1f\x7f]+")


def clean_note(text: Any) -> str:
    """One line, no control characters, no backticks, at most MAX_NOTE_CHARS."""
    if not isinstance(text, str):
        return ""
    flat = _CONTROL.sub(" ", text).replace("`", "'")
    return " ".join(flat.split())[:MAX_NOTE_CHARS]


def decision_record(status: str | None, note: str | None) -> dict[str, str] | None:
    """The shape stored with a history candidate, from an approval row's status and note."""
    if status == "rejected":
        decision = "rejected"
    elif status in ("approved", "executed", "execution_failed"):
        decision = "approved"
    else:
        return None
    return {"decision": decision, "note": clean_note(note)}


def decision_line(record: Any) -> str:
    """The line that goes into the prompt, or an empty string."""
    if not isinstance(record, dict) or record.get("decision") not in ("approved", "rejected"):
        return ""
    verb = "APPROVED" if record["decision"] == "approved" else "REJECTED"
    note = clean_note(record.get("note"))
    return f"human review of that earlier incident: {verb} by a reviewer" + (f' - note: "{note}"' if note else "")
