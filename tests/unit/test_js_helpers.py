"""The console's pure helpers are tested with Node's built-in runner (tests/js); this runs them under pytest/CI."""

import pathlib
import shutil
import subprocess

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]


def test_console_helpers_pass_their_node_tests():
    node = shutil.which("node")
    if node is None:
        pytest.skip("node is not installed")
    proc = subprocess.run(  # noqa: S603 - fixed arguments, no untrusted input
        [node, "--test", str(ROOT / "tests" / "js" / "util.test.js")],
        capture_output=True, text=True, timeout=120, check=False,
    )  # fmt: skip
    assert proc.returncode == 0, proc.stdout[-1500:] + proc.stderr[-500:]
