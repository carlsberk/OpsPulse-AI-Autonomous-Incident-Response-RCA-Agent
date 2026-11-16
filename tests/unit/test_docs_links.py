"""Every relative link and `#anchor` in the published markdown resolves, so splitting or renaming a section cannot leave
a dead link (the README is long and was split into several pages; GitHub does not warn about broken anchors)."""

import posixpath
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
PAGES = [
    "README.md",
    "CONTRIBUTING.md",
    "SECURITY.md",
    *sorted(p.relative_to(ROOT).as_posix() for p in (ROOT / "docs").glob("*.md")),
]
LINK = re.compile(r"\]\(([^)\s]+)\)")
EXTERNAL = re.compile(r"^([a-z][a-z0-9+.-]*:|//)", re.IGNORECASE)


def _outside_fences(text: str) -> list[str]:
    out, fenced = [], False
    for line in text.split("\n"):
        if line.startswith("```"):
            fenced = not fenced
        elif not fenced:
            out.append(line)
    return out


def _without_code(text: str) -> list[str]:
    return [re.sub(r"`[^`]*`", "", line) for line in _outside_fences(text)]  # inline code can look like a link


def _slug(heading: str) -> str:
    text = re.sub(r"^#+\s*", "", heading).strip().lower()
    return re.sub(r"\s", "-", re.sub(r"[^\w\s-]", "", text))


def _anchors(path: Path) -> set[str]:
    seen: dict[str, int] = {}
    anchors = set()
    for line in _outside_fences(path.read_text(encoding="utf-8")):  # headings keep their inline code, as on GitHub
        if re.match(r"^#{1,6} ", line):
            slug = _slug(line)
            count = seen.get(slug, 0)
            seen[slug] = count + 1
            anchors.add(slug if count == 0 else f"{slug}-{count}")
    return anchors


@pytest.mark.parametrize("page", PAGES)
def test_every_relative_link_and_anchor_resolves(page):
    base = posixpath.dirname(page)
    broken = []
    for line in _without_code((ROOT / page).read_text(encoding="utf-8")):
        for target in LINK.findall(line):
            if EXTERNAL.match(target):
                continue
            path, _, fragment = target.partition("#")
            normalized = posixpath.normpath(posixpath.join(base, path)) if path else page
            if normalized.startswith(".."):
                continue  # a GitHub-relative URL such as ../../security/advisories/new, not a file in the repository
            resolved = ROOT / normalized
            if not resolved.exists():
                broken.append(f"{target}: no such file")
            elif fragment and resolved.suffix == ".md" and fragment not in _anchors(resolved):
                broken.append(f"{target}: no such heading")
    assert not broken, f"{page} has broken links:\n  " + "\n  ".join(broken)
