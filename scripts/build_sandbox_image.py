"""Build the sandbox image for one repository: pytest plus the dependencies that repository declares.

    python -m scripts.build_sandbox_image owner/repo                 # downloads the default branch
    python -m scripts.build_sandbox_image owner/repo --from-dir ./checkout --tag my-sandbox
    python -m scripts.build_sandbox_image owner/repo --dry-run       # print the Dockerfile and the dependency list only

The sandbox runs the tests with NO network, so every dependency must already be in the image; a bare `python:3.11-slim`
ends as an `error` verdict ("pytest missing"). This reads `requirements*.txt`, `requirements/*.txt` and the
dependencies and test/dev extras of `pyproject.toml`, and builds an image with them. A private repository needs
GITHUB_TOKEN. Then set `SANDBOX_IMAGE` to the printed tag.

Safety: `docker build` installs third-party packages WITH network access (in a build container, not on this machine).
By default only prebuilt wheels are installed (`--only-binary=:all:`), so no package's `setup.py` runs; a
dependency that has no wheel makes the build fail, and `--allow-source-builds` lifts that for a repository you trust.
Dependencies that are not plain package names (VCS or URL links, local paths, editable installs) are skipped and listed.
The repository's own source is not installed: the sandbox copies it next to the tests and puts it on `PYTHONPATH`.
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
import tempfile
import tomllib
from collections.abc import Callable
from pathlib import Path

from packaging.requirements import InvalidRequirement, Requirement
from packaging.utils import canonicalize_name

TEST_EXTRAS = frozenset({"test", "tests", "testing", "dev", "develop", "development"})
REQUIREMENT_GLOBS = ("requirements*.txt", "requirements/*.txt")
DEFAULT_BASE = "python:3.11-slim"
MAX_DEPENDENCIES = 300


def _clean(line: str) -> str:
    """One requirements line without comments, hashes and line continuations."""
    line = line.split(" #")[0] if " #" in line else line
    line = re.sub(r"\s--hash=\S+", "", line).rstrip("\\ \t")
    return line.strip()


def from_requirements_file(text: str) -> tuple[list[str], list[str]]:
    """(plain requirement lines, skipped lines) from the text of a requirements file."""
    found: list[str] = []
    skipped: list[str] = []
    for raw in text.splitlines():
        line = _clean(raw)
        if not line or line.startswith("#"):
            continue
        if line.startswith("-") or "://" in line or "@" in line.split(";")[0] or line.startswith((".", "/")):
            skipped.append(line)
            continue
        found.append(line)
    return found, skipped


def from_pyproject(text: str) -> list[str]:
    """Runtime dependencies plus the test/dev extras and dependency groups of a pyproject.toml."""
    data = tomllib.loads(text)
    project = data.get("project", {})
    found: list[str] = list(project.get("dependencies", []))
    for extra, items in project.get("optional-dependencies", {}).items():
        if extra.lower() in TEST_EXTRAS:
            found += [i for i in items if isinstance(i, str)]
    for group, items in data.get("dependency-groups", {}).items():
        if group.lower() in TEST_EXTRAS:
            found += [i for i in items if isinstance(i, str)]  # `{include-group = ...}` entries are skipped
    return found


def collect(root: Path) -> tuple[list[str], list[str], list[str]]:
    """(dependencies, skipped lines, source files) for the checkout at `root`. pytest is always included."""
    raw: list[str] = []
    skipped: list[str] = []
    sources: list[str] = []
    for pattern in REQUIREMENT_GLOBS:
        for path in sorted(root.glob(pattern)):
            lines, skip = from_requirements_file(path.read_text(encoding="utf-8", errors="replace"))
            raw += lines
            skipped += skip
            sources.append(path.relative_to(root).as_posix())
    pyproject = root / "pyproject.toml"
    if pyproject.is_file():
        try:
            raw += from_pyproject(pyproject.read_text(encoding="utf-8"))
            sources.append("pyproject.toml")
        except tomllib.TOMLDecodeError:
            skipped.append("pyproject.toml (not valid TOML)")
    deps: dict[str, str] = {}
    for line in ["pytest", *raw]:
        try:
            req = Requirement(line)
        except InvalidRequirement:
            skipped.append(line)
            continue
        deps.setdefault(canonicalize_name(req.name), str(req))  # first declaration of a package wins
    return list(deps.values())[:MAX_DEPENDENCIES], skipped, sources


def dockerfile(base: str, binary_only: bool) -> str:
    only = " --only-binary=:all:" if binary_only else ""
    return (
        f"FROM {base}\n"
        "COPY dependencies.txt /tmp/dependencies.txt\n"
        f"RUN pip install --no-cache-dir{only} -r /tmp/dependencies.txt\n"
        "ENV PYTHONPATH=/tmp/work:/tmp/work/src\n"
    )


Runner = Callable[[list[str]], int]


def _run(argv: list[str]) -> int:
    return subprocess.run(argv, check=False).returncode


def default_tag(repo: str) -> str:
    name = re.sub(r"[^a-z0-9_.-]+", "-", repo.split("/")[-1].lower()).strip("-")
    return f"opspulse-sandbox-{name}" if name else "opspulse-sandbox"


def build(root: Path, tag: str, base: str, binary_only: bool, run: Runner = _run) -> int:
    deps, _, _ = collect(root)
    with tempfile.TemporaryDirectory(prefix="opspulse-image-") as tmp:
        context = Path(tmp)
        (context / "dependencies.txt").write_text("\n".join(deps) + "\n", encoding="utf-8")
        (context / "Dockerfile").write_text(dockerfile(base, binary_only), encoding="utf-8")
        return run(["docker", "build", "-t", tag, str(context)])


def _snapshot(repo: str, dest: Path) -> None:
    from src.integrations.github import download_repository_snapshot

    download_repository_snapshot(repo, dest, 50 * 1024 * 1024)


def main(argv: list[str] | None = None, run: Runner = _run) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("repo", help="owner/repo (used for the default tag and, without --from-dir, the download)")
    parser.add_argument("--from-dir", type=Path, help="use this local checkout instead of downloading")
    parser.add_argument("--tag", help="image tag (default: opspulse-sandbox-<repo>)")
    parser.add_argument("--base", default=DEFAULT_BASE, help=f"base image (default {DEFAULT_BASE})")
    parser.add_argument("--allow-source-builds", action="store_true", help="also install packages that have no wheel")
    parser.add_argument("--dry-run", action="store_true", help="print the plan and the Dockerfile, build nothing")
    args = parser.parse_args(argv)
    tag = args.tag or default_tag(args.repo)

    with tempfile.TemporaryDirectory(prefix="opspulse-checkout-") as tmp:
        root = args.from_dir
        if root is None:
            root = Path(tmp) / "repo"
            try:
                _snapshot(args.repo, root)
            except Exception as exc:  # noqa: BLE001 - any download failure: say it plainly, no traceback
                print(f"cannot download {args.repo}: {type(exc).__name__}: {exc}", file=sys.stderr)
                return 2
        deps, skipped, sources = collect(root)
        print(f"read: {', '.join(sources) or 'no dependency files found (only pytest will be installed)'}")
        print(f"{len(deps)} packages: {', '.join(deps[:12])}{' ...' if len(deps) > 12 else ''}")
        if skipped:
            print(f"skipped {len(skipped)} (not plain package names): {'; '.join(skipped[:5])}")
        if args.dry_run:
            print("\n" + dockerfile(args.base, not args.allow_source_builds))
            return 0
        code = build(root, tag, args.base, not args.allow_source_builds, run)
    if code == 0:
        print(
            f"\nbuilt {tag}. Set SANDBOX_IMAGE={tag} (and SANDBOX_ENABLED=true), then run scripts.pilot_check --online"
        )
    else:
        print("\nthe build failed; a dependency without a wheel needs --allow-source-builds", file=sys.stderr)
    return code


if __name__ == "__main__":
    raise SystemExit(main())
