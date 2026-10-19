"""Reproducible end-to-end demonstration on examples/demo_service (no GitHub, no database needed).

    python -m scripts.demo            # scripted model reply, clearly labelled MOCK
    python -m scripts.demo --live     # real Groq model (needs GROQ_API_KEY)

Steps: copy the service to a temp dir -> run its tests (expect a failure) -> reproduce the crash in a
subprocess and capture the real traceback -> run the OpsPulse graph on it -> if the quality gate accepts the
analysis, apply the patch to the temp copy (pure-Python diff application, never `exec`) -> run the tests again
in a subprocess. Results are printed and written to evals/reports/demo-<timestamp>.json.

PR creation is NOT exercised here: with GitHub configured the service would stop at `awaiting_approval` until
a human approves via POST /incidents/{id}/remediation/decision.
"""

from __future__ import annotations

import argparse
import json
import os
import pathlib
import shutil
import subprocess
import sys
import tempfile
from datetime import UTC, datetime

os.environ.setdefault("DATABASE_URL", "sqlite://")

from evals.dataset import EvalCase, Expected
from evals.harness import ScriptedModel, local_history, local_source_fetcher
from src.agent.graph import build_graph
from src.agent.patching import PatchError, apply_unified_diff
from src.agent.state import initial_state
from src.config import settings
from src.integrations.observability import build_run_config

ROOT = pathlib.Path(__file__).resolve().parents[1]
SERVICE = ROOT / "examples" / "demo_service"
REPORTS = ROOT / "evals" / "reports"
MOCK_REPLY = {
    "category": "null_reference",
    "quote": "if available(sku) >= quantity:",
    "fix": {"old": "    return STOCK.get(sku)\n", "new": "    return STOCK.get(sku, 0)\n"},
    "confidence": 0.7,
}


def run(cmd: list[str], cwd: pathlib.Path) -> subprocess.CompletedProcess[str]:
    """Run a command in the sandbox copy ignoring PYTHON* env vars and user site-packages (-E -s)."""
    return subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, timeout=120, check=False)


def pytest_cmd() -> list[str]:
    return [sys.executable, "-E", "-s", "-m", "pytest", "-q", "-p", "no:cacheprovider", "test_inventory.py"]


def step(title: str) -> None:
    print(f"\n=== {title}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--live", action="store_true", help="use the configured Groq model instead of a mock")
    args = parser.parse_args(argv)
    if args.live and not settings.groq_api_key:
        raise SystemExit("--live needs GROQ_API_KEY")
    report: dict = {"started_at": datetime.now(UTC).isoformat(), "model": settings.model_name if args.live else "MOCK"}

    with tempfile.TemporaryDirectory(prefix="opspulse-demo-") as tmp:
        work = pathlib.Path(tmp) / "service"
        shutil.copytree(SERVICE, work, ignore=shutil.ignore_patterns("__pycache__", ".pytest_cache"))

        step("1. Service tests before any change")
        before = run(pytest_cmd(), work)
        print(before.stdout.strip().splitlines()[-1])
        report["tests_before"] = {"exit_code": before.returncode, "summary": before.stdout.strip().splitlines()[-1]}

        step("2. Reproduce the production crash")
        crash = run([sys.executable, "-E", "-s", "app.py"], work)
        trace = (
            crash.stderr.replace(str(work.resolve()) + os.sep, "/app/")
            .replace(str(work) + os.sep, "/app/")
            .replace(os.sep, "/")
            .strip()
        )
        print(trace)
        error = trace.splitlines()[-1]

        step(f"3. OpsPulse analysis ({'LIVE ' + settings.model_name if args.live else 'MOCK scripted reply'})")
        files = {p.name: p.read_text(encoding="utf-8") for p in work.glob("*.py")}
        case = EvalCase(
            id="demo",
            scenario="Unknown SKU crashes reservation",
            repo_name="demo/inventory",
            error_message=error,
            stack_trace=trace,
            files=files,
            history=[],
            expected=Expected(root_cause_category="null_reference", inconclusive=False),
            mock_replies=[MOCK_REPLY],
        )
        factory = None
        if not args.live:
            model = ScriptedModel(case)
            factory = lambda temperature: model  # noqa: E731
        graph = build_graph(local_source_fetcher(case), local_history(case), factory)
        final = graph.invoke(
            initial_state("demo", error, trace, "demo/inventory"),
            config=build_run_config("demo", "demo/inventory", "demo"),
        )
        analysis = final["root_cause_analysis"] or {}
        print(
            f"workflow status: {final['workflow_status']}  attempts: {final['iterations']}  "
            f"quality score: {final['quality_score']:.2f}  gate passed: {final['quality_gate_passed']}"
        )
        print(f"category: {analysis.get('root_cause_category')}")
        print(
            "root cause:",
            ((analysis.get("diagnostic_chain") or {}).get("primary_root_cause") or {}).get("technical_explanation"),
        )
        print("patch:\n" + (final["suggested_patch"] or "(none)"))
        report["analysis"] = {
            "workflow_status": final["workflow_status"],
            "attempts": final["iterations"],
            "quality_score": final["quality_score"],
            "gate_passed": final["quality_gate_passed"],
            "category": analysis.get("root_cause_category"),
            "patch": final["suggested_patch"],
            "error_category": final["error_category"],
        }

        step("4. Apply the patch to the sandbox copy and re-run the tests")
        report["tests_after"] = None
        if final["quality_gate_passed"] and final["suggested_patch"] and final["affected_file"]:
            target = work / final["affected_file"]
            try:
                target.write_text(
                    apply_unified_diff(target.read_text(encoding="utf-8"), final["suggested_patch"]), encoding="utf-8"
                )
            except PatchError as exc:
                print(f"patch could not be applied: {exc}")
            else:
                after = run(pytest_cmd(), work)
                summary = after.stdout.strip().splitlines()[-1]
                print(summary)
                report["tests_after"] = {"exit_code": after.returncode, "summary": summary}
        else:
            print("quality gate did not accept a patch - nothing applied (incident would go to human review)")

    step("5. Remediation")
    print(
        "Not exercised: with GitHub configured the incident would wait in `awaiting_approval` for a human "
        "decision; no PR is opened or merged automatically. PR creation is untested in this demo."
    )
    REPORTS.mkdir(parents=True, exist_ok=True)
    path = REPORTS / f"demo-{datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')}.json"
    path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(f"\nreport: {path}")
    fixed = report["tests_after"] is not None and report["tests_after"]["exit_code"] == 0
    print("RESULT:", "tests pass after the proposed patch" if fixed else "fix NOT verified")
    return 0 if fixed else 1


if __name__ == "__main__":
    raise SystemExit(main())
