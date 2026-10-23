"""Build an RCA dataset from the bug-fix HISTORY of a real repository, and check proposed patches against it.

    python -m evals.history_real_repo build --repo-dir /path/to/clean/clone \
        --python /path/to/venv/python --repo-name owner/repo --out history.json
    python -m evals.run_rca --mode live --dataset-file history.json
    python -m evals.history_real_repo verify --repo-dir /path/to/clean/clone --python /path/to/venv/python \
        --dataset history.json --report evals/reports/rca-....json

Unlike `evals.pilot_real_repo` nothing is injected. A case is a real commit that fixed a bug and added or changed a
test: the parent commit plus that commit's tests fail with a native traceback (the incident), and the real fix makes
them pass
(so the failure is genuinely this bug). The truth is the real fix: its files are the relevant files, and `verify`
applies the model's patch to the parent and runs the same tests, which is a far better measure than "the patch applies".

Limits worth stating:
* The category label is a heuristic from the exception type (a bug's real cause is rarely one of ten tidy labels).
  Read `relevant_file_hit_rate` and the `verify` result, not `category_accuracy`.
* The repository's code and tests run on THIS machine (in a throwaway worktree, with the interpreter you pass). Use a
  small, well-known library and a virtualenv without your credentials in the environment.
* Libraries with few bug-fix commits give few cases; that is a proof of method, not a benchmark.
"""

from __future__ import annotations

import argparse
import json
import pathlib
import re
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass

from evals.pilot_real_repo import _native_failure
from src.agent.patching import PatchError, apply_unified_diff

FIX_WORDS = r"fix|bug|crash|error|exception|regression|raise|wrong|incorrect|fail"
SKIP_SUBJECT = re.compile(
    r"typo|docs?\b|documentation|lint|format|style|ci\b|tox|python 3\.\d+|deprecat", re.IGNORECASE
)
TEST_PATH = re.compile(r"(^|/)(tests?|testing)/|(^|/)test_[^/]*\.py$|_test\.py$")
MAX_SOURCE_LINES_CHANGED = 40
MAX_FILES_IN_CASE = 40
MAX_CASE_BYTES = 400_000
TEST_TIMEOUT = 180

CATEGORY_BY_ERROR = (
    (r"KeyError", "missing_key"),
    (r"AttributeError: 'NoneType'|TypeError: 'NoneType'|NoneType", "null_reference"),
    (r"AttributeError", "attribute_error"),
    (r"ImportError|ModuleNotFoundError", "import_error"),
    (r"TypeError|ValueError", "type_error"),
)


@dataclass(frozen=True)
class Candidate:
    sha: str
    parent: str
    subject: str
    source_files: tuple[str, ...]
    test_files: tuple[str, ...]


def git(repo: pathlib.Path, *args: str) -> str:
    done = subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True, check=False)  # noqa: S603, S607
    if done.returncode != 0:
        raise RuntimeError(f"git {' '.join(args[:2])} failed: {done.stderr.strip()[:200]}")
    return done.stdout


def find_candidates(repo: pathlib.Path, limit: int, since: str) -> list[Candidate]:
    log = git(
        repo, "log", "--no-merges", f"--since={since}", "-i", "-E", f"--grep={FIX_WORDS}", "--format=%H%x09%P%x09%s"
    )
    found: list[Candidate] = []
    for line in log.splitlines():
        sha, parents, subject = line.split("\t", 2)
        if not parents or " " in parents or SKIP_SUBJECT.search(subject):
            continue  # root commit, merge, or not a behaviour fix
        names = [n for n in git(repo, "show", "--name-only", "--format=", sha).splitlines() if n.strip()]
        tests = tuple(n for n in names if n.endswith(".py") and TEST_PATH.search(n))
        sources = tuple(n for n in names if n.endswith(".py") and not TEST_PATH.search(n) and "/" in n)
        if not tests or not 1 <= len(sources) <= 2 or len(sources) + len(tests) != len(names):
            continue
        stat = git(repo, "show", "--numstat", "--format=", sha, "--", *sources).split()
        changed = sum(int(x) for x in stat[0::3] if x.isdigit()) + sum(int(x) for x in stat[1::3] if x.isdigit())
        if changed > MAX_SOURCE_LINES_CHANGED:
            continue
        found.append(Candidate(sha, parents, subject, sources, tests))
        if len(found) >= limit:
            break
    return found


def run_tests(python: str, work: pathlib.Path, tests: tuple[str, ...]) -> tuple[int, str]:
    env = {"PATH": "/usr/bin:/bin", "PYTHONPATH": str(work), "PYTHONDONTWRITEBYTECODE": "1", "HOME": str(work)}
    try:
        done = subprocess.run(  # noqa: S603 - fixed argument list, minimal environment (no credentials)
            [python, "-m", "pytest", *tests, "-x", "-q", "--tb=native", "-p", "no:cacheprovider"],
            cwd=work,
            env=env,
            capture_output=True,
            text=True,
            timeout=TEST_TIMEOUT,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return 124, ""
    return done.returncode, done.stdout + done.stderr


def _category(error: str) -> str:
    return next((cat for pattern, cat in CATEGORY_BY_ERROR if re.search(pattern, error)), "logic_error")


def _snapshot(work: pathlib.Path, sources: tuple[str, ...]) -> dict[str, str]:
    package = sources[0].split("/")[0]
    files: dict[str, str] = {}
    total = 0
    for path in sorted((work / package).rglob("*.py")):
        if TEST_PATH.search(path.relative_to(work).as_posix()):
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        total += len(text)
        if len(files) >= MAX_FILES_IN_CASE or total > MAX_CASE_BYTES:
            break
        files[path.relative_to(work).as_posix()] = text
    return files


def build_case(repo: pathlib.Path, python: str, cand: Candidate, repo_name: str) -> tuple[dict, dict] | str:
    """The case and its truth, or the reason this commit is not a clean red-to-green."""
    with tempfile.TemporaryDirectory(prefix="opspulse-history-", ignore_cleanup_errors=True) as tmp:
        work = pathlib.Path(tmp) / "wt"
        git(repo, "worktree", "add", "--detach", str(work), cand.parent)
        try:
            git(work, "checkout", cand.sha, "--", *cand.test_files)
            code, output = run_tests(python, work, cand.test_files)
            failure = _native_failure(output, work.resolve())  # tracebacks print the resolved path (macOS /private/var)
            if code == 0 or failure is None:
                return "the tests do not fail at the parent with a Python traceback"
            trace, error = failure
            if "\n" in error or "Traceback" in error:
                return "the test failed through a subprocess, so there is no single error line to report"
            if not any(f"/srv/{src}" in trace for src in cand.source_files):
                return "the failure is not in the files the fix changed (import or collection error?)"
            git(work, "checkout", cand.sha, "--", *cand.source_files)
            fixed_code, fixed_out = run_tests(python, work, cand.test_files)
            if fixed_code != 0:
                return f"the real fix does not turn the tests green here: {fixed_out.strip()[-90:]!r}"
            git(work, "checkout", cand.parent, "--", *cand.source_files)
            files = _snapshot(work, cand.source_files)
        finally:
            subprocess.run(["git", "-C", str(repo), "worktree", "remove", "--force", str(work)], check=False)  # noqa: S603, S607
    case_id = f"hist-{cand.sha[:8]}"
    case = {
        "id": case_id,
        "scenario": cand.subject[:120],
        "repo_name": repo_name,
        "error_message": error,
        "stack_trace": trace,
        "files": files,
        "history": [],
        "expected": {
            "root_cause_category": _category(error),
            "inconclusive": False,
            "relevant_files": list(cand.source_files),
            "note": "Category is a heuristic from the exception type; the truth is the real fix commit "
            f"{cand.sha[:12]}. Prefer relevant_file_hit_rate and `history_real_repo verify`.",
        },
        "mock_replies": [{"unused": "live mode only"}],
    }
    meta = {
        "fix_commit": cand.sha,
        "parent": cand.parent,
        "subject": cand.subject,
        "source_files": list(cand.source_files),
        "test_files": list(cand.test_files),
    }
    return case, meta


def verify_patch(repo: pathlib.Path, python: str, meta: dict, patch: str) -> tuple[str, str]:
    """Apply `patch` to the parent commit and run the tests the real fix added. Returns (verdict, detail)."""
    target = meta["source_files"]
    with tempfile.TemporaryDirectory(prefix="opspulse-verify-", ignore_cleanup_errors=True) as tmp:
        work = pathlib.Path(tmp) / "wt"
        git(repo, "worktree", "add", "--detach", str(work), meta["parent"])
        try:
            tests = tuple(meta["test_files"])
            git(work, "checkout", meta["fix_commit"], "--", *tests)
            headers = re.findall(r"^\+\+\+ b/(\S+)", patch, flags=re.MULTILINE)
            if len(headers) != 1:
                return "no_patch", "the run has no single-file patch"
            path = work / headers[0]
            if not path.resolve().is_relative_to(work.resolve()) or not path.is_file():
                return "patch_error", f"patch targets a file that is not in the repository: {headers[0]}"
            try:
                path.write_text(apply_unified_diff(path.read_text(encoding="utf-8"), patch), encoding="utf-8")
            except PatchError as exc:
                return "patch_error", f"patch does not apply: {str(exc)[:120]}"
            code, output = run_tests(python, work, tests)
            where = "the real fix's file" if headers[0] in target else f"another file ({headers[0]})"
            if code == 0:
                return "passes_tests", f"the real regression tests pass; patch is in {where}"
            return "fails_tests", f"the real regression tests still fail; patch is in {where}; {output[-160:]!r}"
        finally:
            subprocess.run(["git", "-C", str(repo), "worktree", "remove", "--force", str(work)], check=False)  # noqa: S603, S607


def cmd_build(args: argparse.Namespace) -> int:
    candidates = find_candidates(args.repo_dir, args.scan, args.since)
    print(f"{len(candidates)} candidate commits (single-purpose bug fixes that changed a test)")
    cases, metas = [], {}
    for cand in candidates:
        built = build_case(args.repo_dir, args.python, cand, args.repo_name)
        if isinstance(built, str):
            print(f"  skip {cand.sha[:8]} {cand.subject[:50]}: {built}")
            continue
        case, meta = built
        print(f"  ok   {cand.sha[:8]} {cand.subject[:60]} -> {case['error_message'][:70]}")
        cases.append(case)
        metas[case["id"]] = meta
        if len(cases) >= args.max_cases:
            break
    args.out.write_text(
        json.dumps({"version": f"history-{args.repo_name.split('/')[-1]}-v1", "cases": cases}, indent=2)
    )
    args.out.with_suffix(".meta.json").write_text(json.dumps(metas, indent=2))
    print(f"wrote {len(cases)} cases to {args.out} (truth in {args.out.with_suffix('.meta.json').name})")
    return 0 if cases else 1


def cmd_verify(args: argparse.Namespace) -> int:
    metas = json.loads(args.dataset.with_suffix(".meta.json").read_text())
    report = json.loads(args.report.read_text())
    counts: dict[str, int] = {}
    for run in report["results"]:
        meta = metas.get(run["case_id"])
        if meta is None:
            continue
        patch = run.get("patch") or ""
        verdict, detail = verify_patch(args.repo_dir, args.python, meta, patch) if patch else ("no_patch", "no patch")
        counts[verdict] = counts.get(verdict, 0) + 1
        accepted = run.get("workflow_status") == "accepted"
        print(f"  {run['case_id']}: {verdict} (gate {'accepted' if accepted else 'did not accept'}) - {detail}")
    print("\nsummary:", json.dumps(counts, sort_keys=True))
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("build", "verify"):
        p = sub.add_parser(name)
        p.add_argument("--repo-dir", type=pathlib.Path, required=True)
        p.add_argument("--python", default=sys.executable, help="interpreter that can run the repository's tests")
        if name == "build":
            p.add_argument("--repo-name", required=True, help="owner/repo, used only as a label")
            p.add_argument("--out", type=pathlib.Path, required=True)
            p.add_argument(
                "--since", default="2019-01-01", help="skip commits older than this (they predate modern Python)"
            )
            p.add_argument("--scan", type=int, default=60, help="how many candidate commits to examine")
            p.add_argument("--max-cases", type=int, default=15)
        else:
            p.add_argument("--dataset", type=pathlib.Path, required=True)
            p.add_argument("--report", type=pathlib.Path, required=True)
    args = parser.parse_args(argv)
    shutil.which("git") or sys.exit("git is required")
    return cmd_build(args) if args.command == "build" else cmd_verify(args)


if __name__ == "__main__":
    raise SystemExit(main())
