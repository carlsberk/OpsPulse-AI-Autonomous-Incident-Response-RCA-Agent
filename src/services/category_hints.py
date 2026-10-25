"""A second look at the diagnosis category, from the code the model was shown. Advice for the reviewer, never a verdict.

The quality gate proves that quotes are real and that a patch applies; it does not check that the *category* fits the
code. One mistake that the live evaluation reproduced in both holdout runs: a `KeyError: 'items'` on `payload["items"]`
where `payload = requests.get(...).json()` was filed as `missing_key` (our code lacks a key), although the code itself
shows that the key is read from an upstream HTTP response, which is `invalid_external_response` (the other service
answered with a different document). The label is a judgement, so this is a hint that tells the reviewer where to look,
not a gate check: it never changes the score, never blocks, and says what it saw.

Deterministic and conservative: Python-style source only, one named variable, an assignment visible in the stored
window.
"""

from __future__ import annotations

import re
from typing import Any

from src.agent.patching import PatchError, parse_code_window

# Categories that say "our code is missing something" and so would hide an upstream problem.
INTERNAL_CATEGORIES = frozenset({"missing_key", "null_reference", "attribute_error", "type_error"})

_VERBS = r"(?:get|post|put|patch|delete|head|request|fetch|urlopen)"
# Module-level calls are unambiguous; a bare `session.get(...)` or `client.get(...)` is not (a web session or a cache is
# not an HTTP client), so those names only count when the window shows them being built from an HTTP client class.
_MODULE_CALL = re.compile(rf"\b(?:requests|httpx|aiohttp|urllib3?|urllib\.request)\s*\.\s*{_VERBS}\s*\(|\burlopen\s*\(")
_CLIENT_BUILD = re.compile(
    r"^\s*(?:async\s+with\s+|with\s+)?([A-Za-z_]\w*)\s*=\s*(?:await\s+)?"
    r"(?:requests\.Session|httpx\.(?:Async)?Client|aiohttp\.ClientSession|urllib3\.PoolManager)\s*\("
)
_IDENT = r"[A-Za-z_][A-Za-z0-9_]*"
_ACCESS = re.compile(rf"\b({_IDENT})\s*(?:\[|\.(?!(?:get|keys|values|items)\b))")
_NOT_DATA_NAMES = frozenset(
    {"self", "cls", "os", "sys", "json", "re", "np", "pd", "logging", "logger", "settings", "config"}
)


def _window_lines(analysis: dict[str, Any]) -> list[str]:
    windows = analysis.get("source_windows")
    lines: list[str] = []
    for block in windows.values() if isinstance(windows, dict) else []:
        try:
            lines.extend(parse_code_window(str(block))[1])
        except PatchError:
            continue
    return lines


def _assignment(lines: list[str], name: str) -> str | None:
    """The right-hand side of the last single-line `name = ...` in the window, or None."""
    pattern = re.compile(rf"^\s*{re.escape(name)}\s*(?::[^=]+)?=\s*(?!=)(.+?)\s*$")
    found = None
    for line in lines:
        match = pattern.match(line)
        if match:
            found = match.group(1)
    return found


def _client_names(lines: list[str]) -> set[str]:
    return {match.group(1) for line in lines if (match := _CLIENT_BUILD.match(line))}


def _is_http_call(rhs: str, clients: set[str]) -> bool:
    if _MODULE_CALL.search(rhs):
        return True
    return any(re.search(rf"\b{re.escape(name)}\s*\.\s*{_VERBS}\s*\(", rhs) for name in clients)


def _comes_from_http(lines: list[str], name: str, depth: int = 0) -> str | None:
    """The call that `name` was assigned from when that call is an HTTP request (directly, or one hop through a response
    variable such as `response = requests.get(...)` then `payload = response.json()`), else None."""
    rhs = _assignment(lines, name)
    if rhs is None:
        return None
    if _is_http_call(rhs, _client_names(lines)):
        return rhs
    if depth == 0:
        source = re.match(rf"^(?:await\s+)?({_IDENT})\s*(?:\.\s*(?:json|text|content)\b|\[)", rhs)
        if source and source.group(1) != name:
            return _comes_from_http(lines, source.group(1), depth + 1)
    return None


def external_response_hint(analysis: dict[str, Any] | None) -> str | None:
    """A sentence for the reviewer when the diagnosis blames our code but the failing line reads an HTTP response."""
    if not isinstance(analysis, dict) or analysis.get("root_cause_category") not in INTERNAL_CATEGORIES:
        return None
    lines = _window_lines(analysis)
    if not lines:
        return None
    for item in analysis.get("evidence") or []:
        if not (isinstance(item, dict) and item.get("kind") == "observed" and item.get("source") == "code_context"):
            continue
        quote = str(item.get("quote") or "")
        for match in _ACCESS.finditer(quote):
            name = match.group(1)
            if name in _NOT_DATA_NAMES:
                continue
            call = _comes_from_http(lines, name)
            if call:
                shown = re.sub(r"\s+", " ", call)[:90]
                return (
                    f"The failing statement reads from `{name}`, which the code shows is the result of an HTTP call "
                    f"(`{name} = {shown}`). A missing key or attribute there usually means the other service answered "
                    f"with a different document (category invalid_external_response), not that this code lacks it. "
                    f"Check what the upstream actually returned before accepting a guard or default."
                )
    return None
