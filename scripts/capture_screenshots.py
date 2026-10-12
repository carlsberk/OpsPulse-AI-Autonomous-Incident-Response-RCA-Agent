"""Regenerate the console screenshots in docs/images from the demo data (needs Edge or Chrome installed).

    python -m scripts.capture_screenshots              # light and dark, list and incident detail
    python -m scripts.capture_screenshots --port 8791

It starts `scripts.demo_console` (throwaway database, scripted replies, local demo key), asks the API which
incident has the most interesting history, and takes headless-browser screenshots of the real console. Nothing
here is mocked up: the images are what the shipped page renders for that data.
"""

from __future__ import annotations

import argparse
import json
import os
import pathlib
import shutil
import subprocess
import sys
import time
import urllib.request

ROOT = pathlib.Path(__file__).resolve().parents[1]
IMAGES = ROOT / "docs" / "images"
DEMO_KEY = "demo-key"
BROWSERS = (
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    "/usr/bin/google-chrome",
    "/usr/bin/chromium",
    "/usr/bin/chromium-browser",
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
)


def find_browser() -> str | None:
    for candidate in BROWSERS:
        if os.path.exists(candidate):
            return candidate
    for name in ("msedge", "google-chrome", "chromium", "chrome"):
        found = shutil.which(name)
        if found:
            return found
    return None


def _get(url: str, key: str = "") -> dict:
    request = urllib.request.Request(url, headers={"X-API-Key": key} if key else {})  # noqa: S310 - local URL
    with urllib.request.urlopen(request, timeout=10) as response:  # noqa: S310
        return json.loads(response.read())


def wait_ready(base: str, seconds: int = 90) -> None:
    deadline = time.time() + seconds
    while time.time() < deadline:
        try:
            if _get(f"{base}/healthz").get("status") == "ok":
                return
        except OSError:
            time.sleep(0.5)
    raise SystemExit("the demo server did not start")


def pick_incident(base: str, status: str | None = None) -> str | None:
    """The incident with the longest attempt history (optionally of one status)."""
    items = _get(f"{base}/incidents?limit=50", DEMO_KEY)["items"]
    items = [i for i in items if status is None or i["status"] == status]
    if not items:
        return None
    return max(items, key=lambda i: (i["iterations"], i["status"] == "needs_review"))["incident_id"]


def shoot(browser: str, url: str, out: pathlib.Path, size: tuple[int, int]) -> None:
    """The theme and status filter are chosen by the page's own `?theme=` / `?status=` options, so the result does
    not depend on the browser's colour-scheme flags or on the machine's theme."""
    out.parent.mkdir(parents=True, exist_ok=True)
    command = [
        browser, "--headless=new", "--disable-gpu", "--hide-scrollbars", "--force-device-scale-factor=1",
        f"--window-size={size[0]},{size[1]}", "--virtual-time-budget=7000", f"--screenshot={out}", url,
    ]  # fmt: skip
    subprocess.run(command, check=True, capture_output=True, timeout=120)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--port", type=int, default=8790)
    parser.add_argument("--out", type=pathlib.Path, default=IMAGES)
    args = parser.parse_args(argv)

    browser = find_browser()
    if browser is None:
        print("no Edge/Chrome/Chromium found", file=sys.stderr)
        return 2
    base = f"http://127.0.0.1:{args.port}"
    server = subprocess.Popen(
        [sys.executable, "-m", "scripts.demo_console", "--port", str(args.port)],
        cwd=ROOT, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )  # fmt: skip
    try:
        wait_ready(base)
        incident = pick_incident(base)
        approval = pick_incident(base, "awaiting_approval")
        if incident is None:
            raise SystemExit("no demo incidents")
        for theme in ("light", "dark"):
            query = f"?theme={theme}&status=all"
            shoot(browser, f"{base}/console{query}#/", args.out / f"console-list-{theme}.png", (1360, 900))
            shoot(browser, f"{base}/console{query}#/incident/{incident}", args.out / f"console-detail-{theme}.png",
                  (1360, 2650))  # fmt: skip
            if approval:
                shoot(browser, f"{base}/console{query}#/incident/{approval}",
                      args.out / f"console-approval-{theme}.png", (1360, 2650))  # fmt: skip
            print(f"wrote console-list/detail/approval-{theme}.png")
    finally:
        server.terminate()
        try:
            server.wait(timeout=10)
        except subprocess.TimeoutExpired:
            server.kill()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
