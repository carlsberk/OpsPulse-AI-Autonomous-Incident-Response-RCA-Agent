"""The git revision an incident was produced by, when the reporter knows it."""

from __future__ import annotations

import re
from typing import Any

_SHA = re.compile(r"[0-9a-f]{7,40}")


def normalize_commit_sha(value: Any) -> str | None:
    """A 7-40 character hexadecimal git SHA (lower-cased), or None. Version strings such as "1.4.2" are not SHAs."""
    if not isinstance(value, str):
        return None
    candidate = value.strip().lower()
    return candidate if _SHA.fullmatch(candidate) else None
