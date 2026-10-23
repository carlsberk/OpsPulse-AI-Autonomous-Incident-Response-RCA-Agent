"""Build an RCA dataset from a REAL repository by injecting known bugs into a throwaway copy.

    python -m evals.pilot_real_repo --repo-dir /path/to/clean/clone --python /path/to/venv/python --out pilot.json
    python -m evals.run_rca --mode live --dataset-file pilot.json --repeat 3

The repository itself is never modified: each mutation is applied to a temporary copy, the repository's own test
suite is run there, and the first failing test's real (native) traceback becomes the incident. The truth is known
because we injected the bug: the labelled category and fix location come from the mutation, not from a judgement.

Limits worth stating: injected bugs are simpler and more local than organic production failures, so results here
say "the model can localise a known fault in real code it has never seen", not "it will fix your production incidents".
"""

# ruff: noqa: E501  (the mutations embed source snippets and trigger programs as single-line strings)
from __future__ import annotations

import argparse
import json
import pathlib
import re
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field

TRACE_START = "Traceback (most recent call last):"


@dataclass(frozen=True)
class Edit:
    file: str
    old: str
    new: str


@dataclass(frozen=True)
class Mutation:
    id: str
    scenario: str
    category: str
    fix_files: tuple[str, ...]  # where a correct fix belongs (labelled truth)
    edits: tuple[Edit, ...] = field(default_factory=tuple)
    trigger: str = ""  # python snippet reproducing a realistic crash when the repository's own tests do not reach it
    alternatives: tuple[str, ...] = ()  # other categories a reviewer would accept for the same failure


MUTATIONS = (
    Mutation(
        "finding-codes-missing-key",
        "The evaluator indexes the system output directly instead of defaulting a missing 'findings' list",
        "missing_key",
        ("app/evaluators/deterministic/finding_codes.py",),
        (
            Edit(
                "app/evaluators/deterministic/finding_codes.py",
                'execution.output.get("findings", [])',
                'execution.output["findings"]',
            ),
        ),
        trigger='import asyncio\nfrom app.core.schemas import EvaluationCase, SystemExecution\nfrom app.evaluators.deterministic.finding_codes import FindingCodesCorrectnessEvaluator\n\ncase = EvaluationCase(id="c1", system="internship-coordinator", expected_output={"must_include_finding_codes": ["DAYS_SHORT"]})\nfailed_call = SystemExecution(case_id="c1", system="internship-coordinator", output={}, error="ConnectError: refused")\nasyncio.run(FindingCodesCorrectnessEvaluator().evaluate(case, failed_call))\n',
    ),
    Mutation(
        "field-accuracy-empty-division",
        "The guard for 'no checkable fields' was removed, so an empty field list divides by zero",
        "logic_error",
        ("app/evaluators/deterministic/field_accuracy.py",),
        (
            Edit(
                "app/evaluators/deterministic/field_accuracy.py",
                '        if not checkable:\n            return EvaluationResult(\n                evaluator=self.name, passed=None, reason="No expected field values on case."\n            )\n',
                "",
            ),
        ),
        trigger='import asyncio\nfrom app.core.schemas import EvaluationCase, SystemExecution\nfrom app.evaluators.deterministic.field_accuracy import DocumentFieldAccuracyEvaluator\n\ncase = EvaluationCase(id="c2", system="internship-coordinator", expected_output={})\nrun = SystemExecution(case_id="c2", system="internship-coordinator", output={"student_name": "A"})\nasyncio.run(DocumentFieldAccuracyEvaluator().evaluate(case, run))\n',
    ),
    Mutation(
        "dataset-version-bad-group",
        "The version sort key reads a regex group that the pattern does not have",
        "logic_error",
        ("app/core/dataset_manager.py",),
        (
            Edit(
                "app/core/dataset_manager.py",
                "int(match.group(1)) if match else -1",
                "int(match.group(2)) if match else -1",
            ),
        ),
    ),
    Mutation(
        "retrieval-k-default-dropped",
        "A caller lost its default for 'k' and passes None on; the helper then does arithmetic on it",
        "type_error",
        ("app/evaluators/deterministic/retrieval_metrics.py",),
        (
            Edit("app/evaluators/deterministic/retrieval_metrics.py", "docs[:k]", "docs[: k + 0]"),
            Edit(
                "app/evaluators/deterministic/retrieval_metrics.py",
                'k = self.config.get("k", 5)\n        relevant = _relevant_ids(case)\n        if not relevant:',
                'k = self.config.get("k")\n        relevant = _relevant_ids(case)\n        if not relevant:',
            ),
        ),
        alternatives=("configuration_error",),  # the missing config default is as defensible a label as the TypeError
        trigger='import asyncio\nfrom app.core.schemas import EvaluationCase, SystemExecution\nfrom app.evaluators.deterministic.retrieval_metrics import RecallAtKEvaluator\n\ncase = EvaluationCase(id="c4", system="ata-rag", expected_output={"relevant_document_ids": ["d1"]})\nrun = SystemExecution(case_id="c4", system="ata-rag", output={"retrieved_documents": [{"id": "d1"}, {"id": "d2"}]})\nasyncio.run(RecallAtKEvaluator().evaluate(case, run))\n',
    ),
)


def _native_failure(output: str, workdir: pathlib.Path) -> tuple[str, str] | None:
    """(stack trace, final error line) of the first failing test's native traceback, paths made repo-relative."""
    blocks = [b for b in output.split(TRACE_START)[1:]]
    if not blocks:
        return None
    last = TRACE_START + blocks[-1]
    lines = []
    for line in last.splitlines():
        if re.match(r"^(=+|-+) ", line) or line.startswith("====") or line.startswith("FAILED"):
            break
        lines.append(line)
    text = "\n".join(lines).replace(str(workdir) + "\\", "/srv/").replace(str(workdir) + "/", "/srv/")
    text = text.replace("\\", "/")
    final = next((ln for ln in reversed(lines) if ln and not ln.startswith(" ")), "")
    return text.strip(), final.strip()


def build_case(mutation: Mutation, repo_dir: pathlib.Path, python: str, repo_name: str) -> dict | None:
    with tempfile.TemporaryDirectory(prefix="opspulse-pilot-", ignore_cleanup_errors=True) as tmp:
        work = pathlib.Path(tmp) / "repo"
        shutil.copytree(repo_dir, work, ignore=shutil.ignore_patterns(".git", "__pycache__", ".pytest_cache", ".venv"))
        for edit in mutation.edits:
            path = work / edit.file
            text = path.read_text(encoding="utf-8")
            if edit.old not in text:
                raise SystemExit(f"{mutation.id}: pattern not found in {edit.file} (the repository changed?)")
            path.write_text(text.replace(edit.old, edit.new, 1), encoding="utf-8")
        command = (
            [python, "-c", mutation.trigger]
            if mutation.trigger
            else [python, "-m", "pytest", "-x", "-q", "--tb=native", "-p", "no:cacheprovider"]
        )
        done = subprocess.run(  # noqa: S603  (fixed argument list)
            command,
            cwd=work,
            capture_output=True,
            text=True,
            timeout=300,
            check=False,
        )
        failure = _native_failure(done.stdout + done.stderr, work)
        if done.returncode == 0 or failure is None:
            print(f"  {mutation.id}: no failure reproduced (tests or trigger) - skipped")
            return None
        trace, error = failure
        files = {
            p.relative_to(work).as_posix(): p.read_text(encoding="utf-8") for p in sorted((work / "app").rglob("*.py"))
        }
        return {
            "id": mutation.id,
            "scenario": mutation.scenario,
            "repo_name": repo_name,
            "error_message": error,
            "stack_trace": trace,
            "files": files,
            "history": [],
            "expected": {
                "root_cause_category": mutation.category,
                "inconclusive": False,
                "relevant_files": list(mutation.fix_files),
                "alternative_categories": list(mutation.alternatives),
            },
            "mock_replies": [{"unused": "live mode only"}],
        }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--repo-dir", type=pathlib.Path, required=True)
    parser.add_argument("--python", default=sys.executable, help="interpreter that can run the repository's tests")
    parser.add_argument("--repo-name", default="codcreater1/AI-Evaluation")
    parser.add_argument("--out", type=pathlib.Path, required=True)
    args = parser.parse_args(argv)
    cases = []
    for mutation in MUTATIONS:
        case = build_case(mutation, args.repo_dir, args.python, args.repo_name)
        if case:
            print(f"  {mutation.id}: {case['error_message']}")
            cases.append(case)
    args.out.write_text(json.dumps({"version": "pilot-real-repo-v1", "cases": cases}, indent=2), encoding="utf-8")
    print(f"wrote {len(cases)} cases to {args.out}")
    return 0 if cases else 1


if __name__ == "__main__":
    raise SystemExit(main())
