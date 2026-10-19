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


def _shape(value):
    """Keys of a JSON value, recursively (a list is described by its first element): the format, not the values."""
    if isinstance(value, dict):
        return {key: _shape(item) for key, item in sorted(value.items())}
    if isinstance(value, list):
        return [_shape(value[0])] if value else []
    return type(value).__name__ if value is not None else "null"


def test_the_published_example_trace_has_the_shape_of_the_real_api(tmp_path):
    """docs/examples/e2e-trace.json documents the reviewer-facing responses. If an endpoint's fields change, this fails
    until the example is regenerated with `python -m scripts.e2e_flow --dump docs/examples/e2e-trace.json`."""
    fresh = tmp_path / "fresh.json"
    done = subprocess.run(  # noqa: S603  (fixed argument list, our own module)
        [sys.executable, "-m", "scripts.e2e_flow", "--quiet", "--dump", str(fresh)],
        capture_output=True,
        text=True,
        timeout=240,
        cwd=ROOT,
        check=False,
    )
    assert done.returncode == 0, done.stdout + done.stderr[-500:]
    committed = json.loads((ROOT / "docs" / "examples" / "e2e-trace.json").read_text(encoding="utf-8"))
    current = json.loads(fresh.read_text(encoding="utf-8"))
    for section in ("incident", "guidance", "trace", "evidence_graph", "audit"):
        assert _shape(committed[section]) == _shape(current[section]), f"docs example is stale: {section}"
