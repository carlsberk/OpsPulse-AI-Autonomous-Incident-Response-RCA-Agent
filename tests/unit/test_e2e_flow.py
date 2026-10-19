"""The end-to-end flow script is a test too: it needs a clean process because settings are read once at import."""

import json
import pathlib
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parents[2]


def test_alert_to_draft_pr_flow_passes(tmp_path):
    out = tmp_path / "e2e.json"
    done = subprocess.run(  # noqa: S603  (fixed argument list, our own module)
        [sys.executable, "-m", "scripts.e2e_flow", "--quiet", "--json", str(out)],
        capture_output=True,
        text=True,
        timeout=240,
        cwd=ROOT,
        check=False,
    )
    result = json.loads(out.read_text(encoding="utf-8")) if out.exists() else {}
    failed = [s["step"] for s in result.get("steps", []) if not s["ok"]]
    assert done.returncode == 0 and result.get("passed"), f"failed steps: {failed}\n{done.stdout}\n{done.stderr[-800:]}"
    assert len(result["steps"]) >= 25
