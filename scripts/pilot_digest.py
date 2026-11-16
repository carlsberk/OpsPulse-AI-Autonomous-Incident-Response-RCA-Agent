"""A one-screen digest of the incidents waiting for a human, to read before opening them in the console.

    OPSPULSE_API_KEY=<reviewer key> python -m scripts.pilot_digest [--base-url http://127.0.0.1:8000] [--status all]

It states facts the API already holds (what failed, what the model called it, what the sandbox said, how big the patch
is) and gives no recommendation: whether a patch is right is the reviewer's decision. The key is read from the
environment and never printed.
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

Fetch = Callable[[str], Any]

WIDTH = 100


def _fetcher(base_url: str, key: str) -> Fetch:
    def fetch(path: str) -> Any:
        request = urllib.request.Request(base_url.rstrip("/") + path, headers={"X-API-Key": key})  # noqa: S310
        with urllib.request.urlopen(request, timeout=15) as response:  # noqa: S310 - the operator's own API
            return json.load(response)

    return fetch


def _first_line(text: str | None, limit: int = 90) -> str:
    line = (text or "").strip().splitlines()[0] if (text or "").strip() else ""
    return line if len(line) <= limit else line[: limit - 1] + "…"


def _patch_size(patch: str | None) -> str:
    if not patch:
        return "no patch"
    added = sum(1 for line in patch.splitlines() if line.startswith("+") and not line.startswith("+++"))
    removed = sum(1 for line in patch.splitlines() if line.startswith("-") and not line.startswith("---"))
    return f"+{added} -{removed}"


def describe(incident: dict[str, Any]) -> str:
    analysis = incident.get("analysis") or {}
    summary = analysis.get("incident_summary") or {}
    sandbox = analysis.get("sandbox") or {}
    attempts = incident.get("iterations")
    lines = [
        f"{str(incident['incident_id'])[:8]}  {incident['status']}  gate {incident.get('quality_score')}  "
        f"attempts {attempts}",
        f"  title     {_first_line(summary.get('title'))}",
        f"  error     {_first_line(incident.get('error_message'))}",
        f"  model says {analysis.get('root_cause_category') or '?'}; file {incident.get('affected_file') or '?'}; "
        f"patch {_patch_size(incident.get('suggested_patch'))}",
        f"  sandbox   {sandbox.get('status') or 'not run'}"
        + (f": {_first_line(sandbox.get('summary'), 70)}" if sandbox.get("summary") else ""),
        f"  uncertainties the model listed: {len(analysis.get('uncertainties') or [])}",
    ]
    return "\n".join(lines)


def digest(fetch: Fetch, status: str = "awaiting_approval") -> str:
    listing = fetch("/incidents?limit=100" + ("" if status == "all" else f"&status={status}"))
    items = listing.get("items", listing) if isinstance(listing, dict) else listing
    if not items:
        return f"No incidents with status {status}."
    blocks = [describe(fetch(f"/incidents/{item['incident_id']}")) for item in items]
    header = f"{len(items)} incident(s), status {status}. Read the patch and the evidence; decide in the console."
    return "\n\n".join([header, *blocks])


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--status", default="awaiting_approval", help="an incident status, or `all`")
    args = parser.parse_args(argv)
    key = os.environ.get("OPSPULSE_API_KEY", "")
    if not key:
        print("Set OPSPULSE_API_KEY to a reviewer key (it is not read from files and never printed).", file=sys.stderr)
        return 2
    try:
        print(digest(_fetcher(args.base_url, key), args.status))
    except urllib.error.HTTPError as exc:
        print(f"The API answered HTTP {exc.code}: check the key and the role.", file=sys.stderr)
        return 1
    except (urllib.error.URLError, OSError):
        print(f"Cannot reach {args.base_url}: is the API running?", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
