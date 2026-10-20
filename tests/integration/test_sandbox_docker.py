"""Opt-in: the sandbox against a REAL Docker daemon, including a repository whose tests try to escape.

    RUN_DOCKER_TESTS=1 pytest tests/integration/test_sandbox_docker.py -v

Needs Docker and network access for the first image build (`python:3.11-slim` plus pytest). The unit tests use a fake
runner; this file is what shows the container flags really contain hostile code. Nothing here touches GitHub.
"""

import difflib
import os
import shutil
import subprocess
from pathlib import Path

import pytest

from src.config import settings
from src.integrations import sandbox
from src.integrations.sandbox import run_patch_in_sandbox

pytestmark = pytest.mark.skipif(
    os.getenv("RUN_DOCKER_TESTS") != "1" or shutil.which("docker") is None,
    reason="set RUN_DOCKER_TESTS=1 and have Docker to run these tests",
)

SERVICE = Path(__file__).resolve().parents[2] / "examples" / "demo_service"
IMAGE = "opspulse-sandbox-test"
BAD = "    return STOCK.get(sku)\n"
GOOD = "    return STOCK.get(sku, 0)\n"

HOSTILE_TESTS = """
import os, socket, subprocess

def test_cannot_reach_the_network():
    s = socket.socket(); s.settimeout(3)
    try:
        s.connect(("1.1.1.1", 80))
    except OSError:
        return
    raise AssertionError("NETWORK REACHABLE")

def test_cannot_write_outside_tmp():
    for path in ("/etc/pwned", "/src/pwned", "/pwned"):
        try:
            open(path, "w").write("x")
        except OSError:
            continue
        raise AssertionError("WROTE " + path)

def test_is_unprivileged_with_no_capabilities():
    assert os.getuid() == 65534
    caps = [l for l in open("/proc/self/status") if l.startswith("CapEff")][0].split()[1]
    assert int(caps, 16) == 0, caps

def test_cannot_see_host_secrets():
    assert "GROQ_API_KEY" not in os.environ and "GITHUB_TOKEN" not in os.environ
    assert not os.path.exists("/var/run/docker.sock")

def test_a_fork_bomb_is_capped():
    started = 0
    for _ in range(400):
        try:
            subprocess.Popen(["sleep", "5"]); started += 1
        except OSError:
            break
    assert started < 300, started
"""


@pytest.fixture(scope="module", autouse=True)
def image():
    built = subprocess.run(  # noqa: S603
        ["docker", "build", "-q", "-t", IMAGE, "-"],  # noqa: S607
        input="FROM python:3.11-slim\nRUN pip install --no-cache-dir pytest\n",
        capture_output=True,
        text=True,
        timeout=600,
        check=False,
    )
    assert built.returncode == 0, built.stderr[-500:]


@pytest.fixture
def configured():
    object.__setattr__(settings, "sandbox_image", IMAGE)
    object.__setattr__(settings, "sandbox_timeout_seconds", 60)
    yield
    object.__setattr__(settings, "sandbox_image", "python:3.11-slim")
    object.__setattr__(settings, "sandbox_timeout_seconds", 120)


def _diff(path: Path, old: str, new: str) -> str:
    text = path.read_text(encoding="utf-8")
    return "".join(
        difflib.unified_diff(
            text.splitlines(True), text.replace(old, new, 1).splitlines(True), "a/inventory.py", "b/inventory.py"
        )
    )


def _snapshot(source: Path, extra: dict[str, str] | None = None, fixed: bool = False):
    def take(repo, dest, limit):
        shutil.copytree(source, dest, ignore=shutil.ignore_patterns("__pycache__", ".pytest_cache"))
        if fixed:
            path = dest / "inventory.py"
            path.write_text(path.read_text(encoding="utf-8").replace(BAD, GOOD), encoding="utf-8")
        for name, text in (extra or {}).items():
            (dest / name).write_text(text, encoding="utf-8")
        return "a" * 40

    return take


def test_a_real_fix_is_seen_to_turn_failing_tests_green(configured):
    report = run_patch_in_sandbox(
        "o/r", "inventory.py", _diff(SERVICE / "inventory.py", BAD, GOOD), snapshot=_snapshot(SERVICE)
    )
    assert report.status == "fixes_failing_tests", report
    assert report.before.exit_code == 1 and report.after.exit_code == 0


def test_a_patch_that_breaks_passing_tests_is_a_regression(configured, tmp_path):
    fixed = tmp_path / "fixed"
    shutil.copytree(SERVICE, fixed, ignore=shutil.ignore_patterns("__pycache__", ".pytest_cache"))
    (fixed / "inventory.py").write_text(
        (fixed / "inventory.py").read_text(encoding="utf-8").replace(BAD, GOOD), encoding="utf-8"
    )
    breaking = _diff(fixed / "inventory.py", GOOD, "    return STOCK.get(sku, 99)\n")
    report = run_patch_in_sandbox("o/r", "inventory.py", breaking, snapshot=_snapshot(SERVICE, fixed=True))
    assert report.status == "regression", report


def test_hostile_tests_cannot_reach_the_network_the_host_or_more_than_their_share(configured):
    """Five escape attempts written as tests. If the container leaks, one of them FAILS, and so does `after`."""
    report = run_patch_in_sandbox(
        "o/r",
        "inventory.py",
        _diff(SERVICE / "inventory.py", BAD, GOOD),
        snapshot=_snapshot(SERVICE, {"test_hostile.py": HOSTILE_TESTS}),
    )
    assert report.status == "fixes_failing_tests", report.after.output_tail
    assert "8 passed" in report.after.output_tail  # 3 of the service + 5 escape attempts, all contained


def test_an_image_without_pytest_is_an_environment_error_not_a_test_failure(configured):
    object.__setattr__(settings, "sandbox_image", "python:3.11-slim")
    report = run_patch_in_sandbox(
        "o/r", "inventory.py", _diff(SERVICE / "inventory.py", BAD, GOOD), snapshot=_snapshot(SERVICE)
    )
    assert report.status == "error" and "SANDBOX_IMAGE" in report.summary, report
    assert sandbox.classify(report.before, report.after)[0] == "error"
