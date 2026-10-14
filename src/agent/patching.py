"""Pure helpers for unified diffs and numbered code windows (no I/O, no third-party deps).

Used by Developer 1's evaluator (to verify a patch against the fetched source) and by
Developer 2's GitHub integration (to apply the very same patch to the full file).
"""

from __future__ import annotations

import difflib
import re
from dataclasses import dataclass, field

HUNK_RE = re.compile(r"^@@\s*-(?P<os>\d+)(?:,(?P<oc>\d+))?\s+\+(?P<ns>\d+)(?:,(?P<nc>\d+))?\s*@@")
WINDOW_LINE_RE = re.compile(r"^\s*(?P<num>\d+) \| ?(?P<text>.*)$")


class PatchError(ValueError):
    """Raised when a diff is malformed or cannot be applied to the given source."""


@dataclass
class Hunk:
    old_start: int | None
    ops: list[tuple[str, str]] = field(default_factory=list)  # (" " | "+" | "-", text)

    @property
    def old_lines(self) -> list[str]:
        return [t for op, t in self.ops if op in (" ", "-")]

    @property
    def new_lines(self) -> list[str]:
        return [t for op, t in self.ops if op in (" ", "+")]

    @property
    def changed(self) -> int:
        return sum(1 for op, _ in self.ops if op in ("+", "-"))


@dataclass
class ParsedDiff:
    path: str | None
    hunks: list[Hunk]
    file_count: int

    @property
    def changed_lines(self) -> int:
        return sum(h.changed for h in self.hunks)


def _clean_header_path(raw: str) -> str | None:
    raw = raw.strip().split("\t")[0].strip()
    if raw in ("/dev/null", ""):
        return None
    if raw.startswith(("a/", "b/")):
        raw = raw[2:]
    return raw


def parse_unified_diff(diff: str) -> ParsedDiff:
    """Lenient unified-diff parser (does not trust hunk line counts)."""
    if not diff or not diff.strip():
        raise PatchError("empty diff")
    lines = diff.replace("\r\n", "\n").split("\n")
    if lines and lines[-1] == "":  # artefact of a trailing newline, not a context line
        lines.pop()
    lines = [ln for ln in lines if not ln.strip().startswith("```")]
    path: str | None = None
    old_path: str | None = None
    file_count = 0
    hunks: list[Hunk] = []
    current: Hunk | None = None

    for index, line in enumerate(lines):
        next_line = lines[index + 1] if index + 1 < len(lines) else ""
        # A "--- " line directly followed by "+++ " starts a new file, even right after a hunk; without this a
        # second file's header would be misread as a removed line and a multi-file diff would look single-file.
        if line.startswith("--- ") and (current is None or next_line.startswith("+++ ")):
            old_path = _clean_header_path(line[4:])
            current = None
            continue
        if line.startswith("+++ ") and current is None:
            new_path = _clean_header_path(line[4:])
            path = new_path or old_path
            file_count += 1
            continue
        if line.startswith("diff --git") or (
            current is None and line.startswith(("index ", "new file", "deleted file"))
        ):
            current = None
            continue
        if line.startswith("@@"):
            m = HUNK_RE.match(line)
            current = Hunk(old_start=int(m.group("os")) if m else None)
            hunks.append(current)
            continue
        if current is None:
            continue
        if line.startswith("\\"):
            continue
        if line == "":
            current.ops.append((" ", ""))
        elif line[0] in " +-":
            current.ops.append((line[0], line[1:]))
        else:
            raise PatchError(f"malformed hunk line: {line[:60]!r}")

    if not hunks:
        raise PatchError("no hunks found")
    for hunk in hunks:
        if not any(op in "+-" for op, _ in hunk.ops):
            raise PatchError("hunk without any added or removed line")
    if file_count == 0 and path is None:
        file_count = 1
    return ParsedDiff(path=path, hunks=hunks, file_count=file_count)


def path_matches(diff_path: str | None, target: str | None) -> bool:
    """True if the diff header path and the target file refer to the same file (suffix match)."""
    if not diff_path or not target:
        return False
    a = diff_path.strip("/").replace("\\", "/")
    b = target.strip("/").replace("\\", "/")
    return a == b or a.endswith("/" + b) or b.endswith("/" + a)


def _eq(a: str, b: str) -> bool:
    return a.rstrip() == b.rstrip()


def _find(lines: list[str], block: list[str], start: int, expected: int) -> int | None:
    if not block:
        return None
    hits = [
        i
        for i in range(max(start, 0), len(lines) - len(block) + 1)
        if all(_eq(lines[i + k], block[k]) for k in range(len(block)))
    ]
    if not hits:
        return None
    return min(hits, key=lambda i: abs(i - expected))


def _first_mismatch(lines: list[str], block: list[str], first_number: int) -> str:
    """Where does the hunk stop matching the file?

    The model usually copies the first lines right and then miscounts (typically blank lines). "first line not
    found" is the wrong thing to report then, because the first line *is* in the file. This finds the position
    where the longest leading part of the hunk matches and names the first line that differs.
    """
    best_start, best_run = 0, -1
    for start in range(len(lines)):
        run = 0
        while start + run < len(lines) and run < len(block) and _eq(lines[start + run], block[run]):
            run += 1
        if run > best_run:
            best_start, best_run = start, run
    if best_run <= 0 or best_run >= len(block):
        return ""
    found = lines[best_start + best_run] if best_start + best_run < len(lines) else "<end of the retrieved window>"
    return (
        f" The hunk matches the file from line {first_number + best_start} for {best_run} line(s); hunk line "
        f"{best_run + 1} ({block[best_run][:60]!r}) differs from the file's {found[:60]!r}."
    )


def _closest_lines(lines: list[str], block: list[str], first_number: int) -> str:
    """The window of `lines` most similar to the hunk's expected lines, quoted with real line numbers.

    Used only to make the retry feedback precise ("you wrote X, the file has Y"); matching stays strict.
    """
    size = len(block)
    if size == 0 or len(lines) < size:
        return ""
    wanted = [text.strip() for text in block]
    best_ratio, best_index = 0.0, 0
    for i in range(len(lines) - size + 1):
        ratio = sum(difflib.SequenceMatcher(None, lines[i + k].strip(), wanted[k]).ratio() for k in range(size)) / size
        if ratio > best_ratio:
            best_ratio, best_index = ratio, i
    if best_ratio < 0.6:
        return ""
    shown = lines[best_index : best_index + size][:6]
    quoted = " | ".join(f"L{first_number + best_index + k}: {text[:100]!r}" for k, text in enumerate(shown))
    return f" Closest lines in the source - copy them exactly, including indentation: {quoted}"


def apply_hunks(
    lines: list[str],
    hunks: list[Hunk],
    line_offset: int = 0,
    positions: list[int] | None = None,
) -> list[str]:
    """Apply hunks to `lines`. `line_offset` = number of file lines that precede lines[0].

    If `positions` is given, the 1-based ORIGINAL file line where each hunk matched is appended to it.
    """
    work = list(lines)
    cursor = 0
    delta = 0
    for number, hunk in enumerate(hunks, start=1):
        old, new = hunk.old_lines, hunk.new_lines
        if not old:
            raise PatchError(f"hunk {number} has no context/removed lines, cannot locate it")
        hint = (hunk.old_start - 1 - line_offset) if hunk.old_start else cursor
        idx = _find(work, old, cursor, hint + delta)
        if idx is None:
            raise PatchError(
                f"hunk {number}: context/removed lines were not found in the source (first line: {old[0][:70]!r})."
                + _first_mismatch(work, old, line_offset + 1 - delta)
                + _closest_lines(work, old, line_offset + 1 - delta)
            )
        if positions is not None:
            positions.append(idx - delta + line_offset + 1)
        work[idx : idx + len(old)] = new
        cursor = idx + len(new)
        delta += len(new) - len(old)
    return work


def apply_unified_diff(original: str, diff: str, line_offset: int = 0) -> str:
    """Apply a single-file unified diff to `original` text, preserving EOL style."""
    parsed = parse_unified_diff(diff)
    eol = "\r\n" if "\r\n" in original else "\n"
    trailing = original.endswith("\n")
    result = apply_hunks(original.splitlines(), parsed.hunks, line_offset)
    return eol.join(result) + (eol if trailing else "")


# ----------------------------- numbered code windows -----------------------------


def format_code_window(lines: list[str], start_line: int, max_line_len: int = 400) -> str:
    """Render lines as '  <n> | <text>' (n is the 1-based line number in the real file)."""
    out = []
    for offset, text in enumerate(lines):
        text = text if len(text) <= max_line_len else text[:max_line_len] + "…"
        out.append(f"{start_line + offset:>6} | {text}")
    return "\n".join(out)


def parse_code_window(code_context: str) -> tuple[int, list[str]]:
    """Inverse of format_code_window; returns (first_line_number, raw_lines)."""
    numbered: list[tuple[int, str]] = []
    for raw in (code_context or "").splitlines():
        m = WINDOW_LINE_RE.match(raw)
        if m:
            numbered.append((int(m.group("num")), m.group("text")))
    if not numbered:
        raise PatchError("code context contains no numbered source lines")
    return numbered[0][0], [text for _, text in numbered]
