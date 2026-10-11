"""Command-line client for a running OpsPulse API (standard library only).

    python -m src.cli submit --repo owner/repo --error "KeyError: 'x'" --trace-file trace.txt --wait
    python -m src.cli list --status awaiting_approval
    python -m src.cli show <incident-id> [--patch]
    python -m src.cli guidance <incident-id>
    python -m src.cli ask <incident-id> "Why was this not accepted?"
    python -m src.cli retry <incident-id>
    python -m src.cli health
    python -m src.cli demo                  # open the console on a throwaway demo database

Configuration: `--url` / OPSPULSE_URL (default http://127.0.0.1:8000) and `--key` / OPSPULSE_API_KEY.
`--json` prints the raw response for scripting. Exit codes: 0 ok, 1 the API refused or failed, 2 usage/connection.

There is deliberately no `approve` command: the decision needs the reviewer to read the evidence and the patch, so
it stays in the console (and in `POST /incidents/{id}/remediation/decision`).
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request
from collections.abc import Callable
from typing import Any

DEFAULT_URL = "http://127.0.0.1:8000"
Transport = Callable[[str, str, dict[str, str], bytes | None], tuple[int, bytes]]


def _urllib_transport(method: str, url: str, headers: dict[str, str], body: bytes | None) -> tuple[int, bytes]:
    if not url.startswith(("http://", "https://")):
        raise ValueError("the URL must start with http:// or https://")
    request = urllib.request.Request(url, data=body, method=method, headers=headers)  # noqa: S310 - scheme checked
    try:
        with urllib.request.urlopen(request, timeout=120) as response:  # noqa: S310
            return response.status, response.read()
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read()


class ApiError(Exception):
    def __init__(self, status: int, message: str) -> None:
        super().__init__(message)
        self.status = status


def call(
    transport: Transport, base: str, key: str, method: str, path: str, payload: dict[str, Any] | None = None
) -> Any:
    headers = {"Accept": "application/json"}
    if key:
        headers["X-API-Key"] = key
    body = None
    if payload is not None:
        body = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"
    status, raw = transport(method, base.rstrip("/") + path, headers, body)
    try:
        data = json.loads(raw) if raw else None
    except ValueError:
        data = None
    if status >= 400:
        message = (data or {}).get("error", {}).get("message") if isinstance(data, dict) else None
        raise ApiError(status, message or f"HTTP {status}")
    return data


def _read_text(path: str | None) -> str:
    if path is None:
        return ""
    if path == "-":
        return sys.stdin.read()
    with open(path, encoding="utf-8") as handle:
        return handle.read()


def _line(label: str, value: Any) -> str:
    return f"{label:<14}{value if value not in (None, '') else '-'}"


def format_incident(incident: dict[str, Any], show_patch: bool = False) -> str:
    analysis = incident.get("analysis") or {}
    root = ((analysis.get("diagnostic_chain") or {}).get("primary_root_cause") or {}).get("technical_explanation")
    out = [
        _line("incident", incident.get("incident_id")),
        _line("status", str(incident.get("status", "")).replace("_", " ")),
        _line("reason", incident.get("status_reason")),
        _line("category", analysis.get("root_cause_category")),
        _line("repository", incident.get("repo_name")),
        _line("file", incident.get("affected_file")),
        _line(
            "gate score",
            f"{float(incident.get('quality_score') or 0):.2f} after {incident.get('iterations')} attempt(s)",
        ),
    ]
    if incident.get("pr_url"):
        out.append(_line("pull request", incident["pr_url"]))
    if root:
        out += ["", "root cause (model hypothesis):", f"  {root}"]
    observed = [e for e in analysis.get("evidence") or [] if isinstance(e, dict) and e.get("kind") == "observed"]
    if observed:
        out += ["", "observed evidence:"]
        out += [f"  - {e.get('claim')}: {e.get('quote')!r}" for e in observed[:5]]
    if incident.get("pending_approval"):
        out += ["", "A patch is waiting for a reviewer's decision (use the console)."]
    if show_patch and incident.get("suggested_patch"):
        out += ["", incident["suggested_patch"].rstrip()]
    return "\n".join(out)


def format_guidance(guide: dict[str, Any]) -> str:
    out = [f"[{guide.get('state')}] {guide.get('headline')}", guide.get("explanation", "")]
    steps = guide.get("next_steps") or []
    if steps:
        out += ["", "next steps:"]
        out += [f"  {i}. ({s['audience']}) {s['text']}" for i, s in enumerate(steps, start=1)]
    failed = guide.get("failed_checks") or []
    if failed:
        out += ["", "failed gate checks (blocking first):"]
        out += [f"  - {c['name']}{' [blocking]' if c['blocking'] else ''}: {c['advice']}" for c in failed]
    for fact in guide.get("facts") or []:
        out.append(f"  . {fact}")
    return "\n".join(out)


def format_answer(answer: dict[str, Any]) -> str:
    tags = [answer.get("source", "")]
    if answer.get("degraded"):
        tags.append("model unavailable")
    if not answer.get("answerable", True):
        tags.append("not in the record")
    elif not answer.get("grounded", True):
        tags.append("NOT GROUNDED - treat with caution")
    out = [answer.get("answer", ""), "", "[" + ", ".join(t for t in tags if t) + "]"]
    if answer.get("cited_sections"):
        out.append("sections: " + ", ".join(answer["cited_sections"]))
    for quote in answer.get("unverified_quotes") or []:
        out.append(f"not in the record: {quote}")
    out.append(answer.get("disclaimer", ""))
    return "\n".join(out)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="opspulse", description=__doc__.split("\n\n")[0])
    parser.add_argument("--url", default=os.environ.get("OPSPULSE_URL", DEFAULT_URL))
    parser.add_argument("--key", default=os.environ.get("OPSPULSE_API_KEY", ""))
    parser.add_argument("--json", action="store_true", help="print the raw JSON response")
    sub = parser.add_subparsers(dest="command", required=True)

    submit = sub.add_parser("submit", help="report an incident")
    submit.add_argument("--repo", required=True, help="owner/repo (must be allow-listed on the server)")
    submit.add_argument("--error", help="error message text")
    submit.add_argument("--error-file", help="read the error message from a file ('-' for stdin)")
    submit.add_argument("--trace-file", help="read the stack trace from a file ('-' for stdin)")
    submit.add_argument("--id", help="client-chosen UUID (re-sending it is idempotent)")
    submit.add_argument("--wait", action="store_true", help="analyse synchronously and print the result")

    listing = sub.add_parser("list", help="list incidents, newest first")
    listing.add_argument("--status", help="e.g. awaiting_approval, needs_review, failed")
    listing.add_argument("--limit", type=int, default=20)

    show = sub.add_parser("show", help="show one incident")
    show.add_argument("incident_id")
    show.add_argument("--patch", action="store_true", help="also print the proposed patch")

    for name, helptext in (("guidance", "what state it is in and what to do next"), ("retry", "re-queue a failed one")):
        command = sub.add_parser(name, help=helptext)
        command.add_argument("incident_id")

    ask = sub.add_parser("ask", help="ask a grounded question about one incident")
    ask.add_argument("incident_id")
    ask.add_argument("question")

    sub.add_parser("health", help="check liveness and readiness")
    sub.add_parser("demo", help="open the console on a throwaway demo database (runs locally)")
    return parser


def run(args: argparse.Namespace, transport: Transport) -> str:
    def api(method: str, path: str, payload: dict[str, Any] | None = None) -> Any:
        return call(transport, args.url, args.key, method, path, payload)

    if args.command == "health":
        live, ready = api("GET", "/healthz"), api("GET", "/readyz")
        data: Any = {"healthz": live, "readyz": ready}
        text = f"liveness: {live.get('status')}   readiness: {ready.get('status')}"
    elif args.command == "list":
        query = f"?limit={args.limit}" + (f"&status={args.status}" if args.status else "")
        data = api("GET", "/incidents" + query)
        rows = [
            f"{i['incident_id']}  {str(i['status']).replace('_', ' '):<20} {float(i['quality_score']):.2f}  "
            f"{i['repo_name']}  {i.get('affected_file') or '-'}"
            for i in data["items"]
        ]
        text = "\n".join(rows) if rows else "no incidents match"
        if data.get("next_cursor"):
            text += f"\n(more available; cursor {data['next_cursor']})"
    elif args.command == "submit":
        message = args.error if args.error is not None else _read_text(args.error_file)
        if not message.strip():
            raise ValueError("give the error with --error or --error-file")
        payload: dict[str, Any] = {"repo_name": args.repo, "error_message": message}
        if args.trace_file:
            payload["stack_trace"] = _read_text(args.trace_file)
        if args.id:
            payload["incident_id"] = args.id
        data = api("POST", "/webhook/incident" + ("?wait=true" if args.wait else ""), payload)
        text = (
            format_incident(data)
            if args.wait and "analysis" in data
            else (f"accepted: {data.get('incident_id')}  status: {data.get('status')}")
        )
    elif args.command == "show":
        data = api("GET", f"/incidents/{args.incident_id}")
        text = format_incident(data, args.patch)
    elif args.command == "guidance":
        data = api("GET", f"/incidents/{args.incident_id}/guidance")
        text = format_guidance(data)
    elif args.command == "ask":
        data = api("POST", f"/incidents/{args.incident_id}/ask", {"question": args.question})
        text = format_answer(data)
    elif args.command == "retry":
        data = api("POST", f"/incidents/{args.incident_id}/retry")
        text = f"re-queued: {data.get('incident_id')}  status: {data.get('status')}"
    else:  # pragma: no cover - argparse guarantees a known command
        raise ValueError(f"unknown command {args.command}")
    return json.dumps(data, indent=2, ensure_ascii=False) if args.json else text


def main(argv: list[str] | None = None, transport: Transport | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "demo":
        from scripts import demo_console

        return demo_console.main([])
    try:
        print(run(args, transport or _urllib_transport))
    except ApiError as exc:
        print(f"error ({exc.status}): {exc}", file=sys.stderr)
        return 1
    except (ValueError, OSError) as exc:  # bad usage, unreadable file, connection refused
        print(f"error: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
