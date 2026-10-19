"""Assemble the documentation site sources from the files the repository already keeps (nothing is duplicated
in git).

    python -m scripts.build_docs            # writes build/site-src, then: mkdocs build

README, CHANGELOG, SECURITY, CONTRIBUTING, docs/RUNBOOK.md and docs/adr/*.md become site pages. Markdown links
between those files are rewritten to site paths; links to any other file in the repository (source, evaluation
reports, workflows) point to GitHub, so the site never has dead relative links.
"""

from __future__ import annotations

import argparse
import posixpath
import re
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "build" / "site-src"
REPO_URL = "https://github.com/codcreater1/OpsPulse-AI-Autonomous-Incident-Response-RCA-Agent"
LINK = re.compile(r"(\]\()([^)\s]+)(\))")

PAGES = {  # repo path -> site path
    "README.md": "index.md",
    "CHANGELOG.md": "changelog.md",
    "SECURITY.md": "security.md",
    "CONTRIBUTING.md": "contributing.md",
    "docs/RUNBOOK.md": "runbook.md",
    "docs/API.md": "api.md",
    "docs/ARCHITECTURE.md": "architecture.md",
    "docs/LIMITATIONS.md": "limitations.md",
    "docs/TRACE_EXAMPLE.md": "trace-example.md",
}


def page_map(root: Path) -> dict[str, str]:
    pages = dict(PAGES)
    for adr in sorted((root / "docs" / "adr").glob("*.md")):
        pages[f"docs/adr/{adr.name}"] = f"adr/{adr.name}"
    return pages


def rewrite_links(text: str, source: str, pages: dict[str, str]) -> str:
    """Rewrite markdown link targets of one page. `source` is the page's repo path, e.g. 'docs/RUNBOOK.md'."""
    site_source = pages[source]
    base = posixpath.dirname(source)

    def replace(match: re.Match[str]) -> str:
        target = match.group(2)
        if re.match(r"^([a-z][a-z0-9+.-]*:|#|//)", target, re.IGNORECASE):
            return match.group(0)
        path, _, fragment = target.partition("#")
        repo_path = posixpath.normpath(posixpath.join(base, path))
        suffix = f"#{fragment}" if fragment else ""
        if repo_path in pages:
            rel = posixpath.relpath(pages[repo_path], posixpath.dirname(site_source) or ".")
            return f"{match.group(1)}{rel}{suffix}{match.group(3)}"
        if repo_path.startswith("docs/images/"):
            rel = posixpath.relpath(repo_path.removeprefix("docs/"), posixpath.dirname(site_source) or ".")
            return f"{match.group(1)}{rel}{match.group(3)}"
        if repo_path.startswith(".."):
            return match.group(0)
        kind = "tree" if not posixpath.splitext(repo_path)[1] else "blob"
        return f"{match.group(1)}{REPO_URL}/{kind}/main/{repo_path}{suffix}{match.group(3)}"

    return LINK.sub(replace, text)


def build(root: Path = ROOT, out: Path = OUT) -> list[str]:
    if out.exists():
        shutil.rmtree(out)
    pages = page_map(root)
    written = []
    for repo_path, site_path in pages.items():
        source = root / repo_path
        if not source.exists():
            continue
        target = out / site_path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(rewrite_links(source.read_text(encoding="utf-8"), repo_path, pages), encoding="utf-8")
        written.append(site_path)
    images = root / "docs" / "images"
    if images.exists():
        shutil.copytree(images, out / "images")
    return written


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--out", type=Path, default=OUT)
    args = parser.parse_args(argv)
    written = build(ROOT, args.out)
    if "index.md" not in written:
        print("README.md missing: nothing to publish", file=sys.stderr)
        return 1
    print(f"assembled {len(written)} pages in {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
