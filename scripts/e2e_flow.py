"""The whole service flow, end to end, over HTTP: alert -> analysis -> review -> draft PR (GitHub stubbed).

    python -m scripts.e2e_flow            # prints a transcript, exits 1 if any step fails
    python -m scripts.e2e_flow --json out.json

What is real: the demo service's crash and tests (run in a subprocess), the Alertmanager webhook, authentication and
roles, the SQLite database with all migrations, the queue worker, the LangGraph workflow, the quality gate, the
approval protocol, the PR text, the evidence graph, the trace and the review history.
What is a stand-in: the model (a scripted reply, labelled MOCK) and GitHub (a recorder; nothing leaves this machine).
It therefore proves the plumbing and the safety rules, not the model's quality - evaluations measure that.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import pathlib
import shutil
import sys
import tempfile
from typing import Any

ROOT = pathlib.Path(__file__).resolve().parents[1]
REPORTER_KEY, REVIEWER_KEY = "e2e-reporter-key", "e2e-reviewer-key"
REPO = "demo/inventory"
DUMP_NOTE = (
    "One run of scripts/e2e_flow.py with a scripted (MOCK) model and a stubbed GitHub: "
    "real shapes, illustrative values."
)


def _configure(db_path: pathlib.Path) -> None:
    """Set the environment before any `src` import (settings are read once) and migrate a throwaway database."""

    def entry(name: str, role: str, key: str) -> str:
        return f"{name}:{role}:{hashlib.sha256(key.encode()).hexdigest()}"

    os.environ.update(
        {
            "DATABASE_URL": f"sqlite:///{db_path.as_posix()}",
            "API_KEY": "",
            "API_KEYS": ",".join([entry("alice", "reporter", REPORTER_KEY), entry("bob", "reviewer", REVIEWER_KEY)]),
            "ALLOWED_REPOSITORIES": REPO,
            "EMBEDDED_WORKER": "false",
            "GROQ_API_KEY": "",
            "LANGFUSE_PUBLIC_KEY": "",
            "LANGFUSE_SECRET_KEY": "",
            "SENTRY_CLIENT_SECRET": "",
            "ALLOW_UNAUTHENTICATED": "false",
            "ENABLE_GITHUB_REMEDIATION": "true",
            "GITHUB_TOKEN": "e2e-placeholder-not-a-token",
            "REQUIRE_REMEDIATION_APPROVAL": "true",
            "RATE_LIMIT_PER_MINUTE": "1000",
            "LOG_LEVEL": "ERROR",
        }
    )
    os.chdir(ROOT)
    sys.path.insert(0, str(ROOT))
    from alembic import command
    from alembic.config import Config

    command.upgrade(Config(str(ROOT / "alembic.ini")), "head")


class Transcript:
    def __init__(self, quiet: bool) -> None:
        self.steps: list[dict[str, Any]] = []
        self.quiet = quiet

    def check(self, title: str, ok: bool, detail: str = "") -> None:
        self.steps.append({"step": title, "ok": bool(ok), "detail": detail})
        if not self.quiet:
            print(f"  [{'ok' if ok else 'FAIL'}] {title}" + (f"  - {detail}" if detail else ""))

    @property
    def passed(self) -> bool:
        return all(s["ok"] for s in self.steps)


def run(quiet: bool = False) -> dict[str, Any]:
    with tempfile.TemporaryDirectory(prefix="opspulse-e2e-", ignore_cleanup_errors=True) as tmp:
        tmp_path = pathlib.Path(tmp)
        _configure(tmp_path / "e2e.db")

        from fastapi.testclient import TestClient

        from evals.dataset import EvalCase, Expected
        from evals.harness import ScriptedModel, local_history, local_source_fetcher
        from scripts.demo import MOCK_REPLY, SERVICE, pytest_cmd
        from scripts.demo import run as run_in_sandbox
        from src.agent.graph import build_graph
        from src.agent.patching import apply_unified_diff
        from src.integrations.github import PullRequestResult
        from src.main import app
        from src.services import incident_service, remediation_service
        from src.worker import Worker

        log = Transcript(quiet)
        say = (lambda *_: None) if quiet else print

        say("\n1. The service has a real bug")
        work = tmp_path / "service"
        shutil.copytree(SERVICE, work, ignore=shutil.ignore_patterns("__pycache__", ".pytest_cache"))
        before = run_in_sandbox(pytest_cmd(), work)
        log.check("service tests fail before any change", before.returncode != 0)
        crash = run_in_sandbox([sys.executable, "-E", "-s", "app.py"], work)
        trace = (
            crash.stderr.replace(str(work.resolve()) + os.sep, "/app/")
            .replace(str(work) + os.sep, "/app/")
            .replace(os.sep, "/")
            .strip()
        )
        error = trace.splitlines()[-1]
        log.check("the crash produces a real traceback", "Traceback" in trace, error)

        files = {p.name: p.read_text(encoding="utf-8") for p in work.glob("*.py")}
        case = EvalCase(
            id="e2e",
            scenario="Unknown SKU crashes reservation",
            repo_name=REPO,
            error_message=error,
            stack_trace=trace,
            files=files,
            history=[],
            expected=Expected(root_cause_category="null_reference", inconclusive=False),
            mock_replies=[MOCK_REPLY],
        )
        model = ScriptedModel(case)
        graph = build_graph(local_source_fetcher(case), local_history(case), lambda temperature: model)
        incident_service.get_graph = lambda: graph  # type: ignore[assignment]
        pull_requests: list[dict[str, Any]] = []

        def record_pull_request(**kwargs: Any) -> PullRequestResult:
            pull_requests.append(kwargs)
            return PullRequestResult("https://github.com/demo/inventory/pull/1", 1, "opspulse/fix-e2e", False)

        remediation_service.create_fix_pull_request = record_pull_request  # type: ignore[assignment]

        client = TestClient(app)
        reporter = {"Authorization": f"Bearer {REPORTER_KEY}"}
        reviewer = {"X-API-Key": REVIEWER_KEY}

        say("\n2. Alertmanager reports it; the worker analyses it")
        notification = {
            "version": "4",
            "status": "firing",
            "receiver": "opspulse",
            "alerts": [
                {
                    "status": "firing",
                    "labels": {"alertname": "InventoryErrors", "repository": REPO},
                    "annotations": {"summary": "orders failing", "description": error, "stack_trace": trace},
                    "startsAt": "2026-10-19T09:00:00Z",
                    "fingerprint": "e2e0000000000001",
                }
            ],
        }
        resp = client.post("/integrations/alertmanager", json=notification, headers=reporter)
        queued = resp.json()["alerts"][0] if resp.status_code == 202 else {}
        incident_id = queued.get("incident_id")
        log.check("webhook accepted and queued", resp.status_code == 202 and queued.get("outcome") == "queued")
        log.check(
            "a second delivery is a duplicate, not a second incident",
            client.post("/integrations/alertmanager", json=notification, headers=reporter).json()["alerts"][0][
                "outcome"
            ]
            == "duplicate",
        )
        processed = 0
        while Worker().run_once():
            processed += 1
        log.check("the worker processed exactly one incident", processed == 1, f"processed={processed}")

        say("\n3. The analysis waits for a human; nothing was sent to GitHub")
        incident = client.get(f"/incidents/{incident_id}", headers=reviewer).json()
        log.check("status is awaiting_approval", incident["status"] == "awaiting_approval", incident["status"])
        log.check(
            "a patch and a pending approval exist", bool(incident["suggested_patch"] and incident["pending_approval"])
        )
        log.check("no pull request exists yet", not pull_requests)
        log.check(
            "the stored code windows are not in the API response", "source_windows" not in (incident["analysis"] or {})
        )

        say("\n4. What the reviewer sees")
        guidance = client.get(f"/incidents/{incident_id}/guidance", headers=reviewer).json()
        graph_body = client.get(f"/incidents/{incident_id}/evidence-graph", headers=reviewer).json()
        trace_body = client.get(f"/incidents/{incident_id}/trace", headers=reviewer).json()
        summary = graph_body["summary"]
        log.check("guidance says a decision is needed", guidance["state"] in ("ok", "attention"), guidance["headline"])
        log.check(
            "every observed quote is verified",
            summary["observed"] > 0 and summary["observed_verified"] == summary["observed"],
            f"{summary['observed_verified']}/{summary['observed']}",
        )
        flagged = any("touches the code the patch changes" in flag for flag in graph_body["flags"])
        log.check(
            "the graph either anchors the patch on a quote or says it does not",
            summary["anchoring_patch"] >= 1 or flagged,
            f"anchoring={summary['anchoring_patch']}, flags={graph_body['flags']}",
        )
        log.check(
            "the trace carries versions and the request id",
            bool(trace_body["versions"]["prompt_version"] and trace_body["request_id"]),
        )

        say("\n5. Only a reviewer can decide; the submitter cannot")
        approval = incident["pending_approval"]
        decision = {
            "approval_id": approval["approval_id"],
            "patch_sha256": approval["patch_sha256"],
            "decision": "approve",
        }
        path = f"/incidents/{incident_id}/remediation/decision"
        log.check("a reporter key is refused", client.post(path, json=decision, headers=reporter).status_code == 403)
        log.check("no key is refused", client.post(path, json=decision).status_code in (401, 403))
        stale = {**decision, "patch_sha256": "0" * 64}
        log.check(
            "a wrong patch hash is refused as stale", client.post(path, json=stale, headers=reviewer).status_code == 409
        )
        log.check("still no pull request", not pull_requests)

        say("\n6. The reviewer approves: one draft PR, never merged")
        approved = client.post(path, json={**decision, "note": "guard matches the existing contract"}, headers=reviewer)
        log.check("approval accepted", approved.status_code == 200, approved.json().get("status", ""))
        log.check("exactly one pull request was created", len(pull_requests) == 1)
        body = pull_requests[0]["body"] if pull_requests else ""
        log.check(
            "PR text says it needs human review and names the reviewer",
            "requires human review" in body and "bob" in body,
        )
        log.check(
            "a repeated approval conflicts and creates nothing",
            client.post(path, json=decision, headers=reviewer).status_code == 409 and len(pull_requests) == 1,
        )
        recorded = {k for k in dir(remediation_service) if "merge" in k.lower()}
        log.check("there is no merge code path", not recorded)

        say("\n7. The record explains itself")
        audit = client.get(f"/incidents/{incident_id}/audit", headers=reviewer).json()
        events = [e["event"] for e in audit["events"]]
        log.check(
            "review history is complete and ordered",
            events == ["submitted", "analysed", "proposed", "approved", "pr_opened"],
            " > ".join(events),
        )
        approval_event = next((e for e in audit["events"] if e["event"] == "approved"), {})
        log.check(
            "the decision names who and why",
            approval_event.get("actor") == "bob" and "contract" in approval_event.get("detail", ""),
        )

        say("\n8. The proposed fix actually fixes the service")
        target = work / incident["affected_file"].split("/")[-1]
        target.write_text(
            apply_unified_diff(target.read_text(encoding="utf-8"), incident["suggested_patch"]), encoding="utf-8"
        )
        after = run_in_sandbox(pytest_cmd(), work)
        log.check(
            "service tests pass after applying the patch (sandbox copy)",
            after.returncode == 0,
            after.stdout.strip().splitlines()[-1],
        )

        from src.db.client import get_engine

        get_engine().dispose()  # release the SQLite file before the temp directory is removed
        artifacts = {
            "note": DUMP_NOTE,
            "incident": {
                key: incident.get(key)
                for key in ("incident_id", "status", "quality_score", "iterations", "affected_file", "suggested_patch")
            },
            "guidance": guidance,
            "trace": trace_body,
            "evidence_graph": graph_body,
            "audit": audit,
        }
        return {
            "passed": log.passed,
            "model": "MOCK (scripted)",
            "incident_id": incident_id,
            "steps": log.steps,
            "artifacts": artifacts,
        }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--json", type=pathlib.Path, help="also write the result as JSON")
    parser.add_argument("--quiet", action="store_true")
    parser.add_argument("--dump", type=pathlib.Path, help="write the reviewer-facing API responses of the run as JSON")
    args = parser.parse_args(argv)
    result = run(quiet=args.quiet)
    if args.json:
        args.json.write_text(json.dumps(result, indent=2), encoding="utf-8")
    if args.dump:
        args.dump.parent.mkdir(parents=True, exist_ok=True)
        args.dump.write_text(json.dumps(result["artifacts"], indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    failed = [s["step"] for s in result["steps"] if not s["ok"]]
    print(
        f"\nRESULT: {'PASS' if result['passed'] else 'FAIL'}  ({len(result['steps'])} checks, model: {result['model']})"
    )
    for step in failed:
        print("  failed:", step)
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
