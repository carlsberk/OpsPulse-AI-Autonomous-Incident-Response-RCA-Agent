"""Run the target repository's tests in a throwaway Docker container, with and without the proposed patch.

The code under test comes from a repository we do not control, so it only ever runs inside a container started with
no network, a read-only root filesystem, no capabilities, an unprivileged user and caps on memory, CPU, processes and
time. The checkout is mounted read-only and copied to a size-capped tmpfs. Nothing here is a verdict on correctness:
it reports what the repository's own tests did, and the reviewer decides.
"""

from __future__ import annotations

import logging
import shutil
import subprocess
import tempfile
import uuid
from collections.abc import Callable
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from src.agent.patching import PatchError, apply_unified_diff
from src.config import settings
from src.integrations.github import GitHubError, download_repository_snapshot
from src.logging_config import redact

logger = logging.getLogger(__name__)

OUTPUT_TAIL_CHARS = 1500
DOCKER_ERROR_CODES = frozenset({125, 126, 127})

# (argv, timeout seconds) -> (exit code or None on timeout, combined output)
Runner = Callable[[list[str], int], tuple[int | None, str]]
Snapshot = Callable[[str, Path, int], str]


@dataclass(frozen=True)
class SandboxRun:
    exit_code: int | None  # None: timed out
    output_tail: str


@dataclass(frozen=True)
class SandboxReport:
    # fixes_failing_tests | no_regression | still_failing | regression | timeout | error | unavailable
    status: str
    summary: str
    commit_sha: str | None = None
    before: SandboxRun | None = None
    after: SandboxRun | None = None

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def build_docker_command(name: str, checkout: Path) -> list[str]:
    memory = f"{settings.sandbox_memory_mb}m"
    script = f"cp -r /src /tmp/work && cd /tmp/work && {settings.sandbox_command}"
    return [
        "docker", "run", "--rm", "--name", name,
        "--network", "none",
        "--read-only", "--tmpfs", "/tmp:rw,exec,nosuid,size=256m",  # noqa: S108 - container tmpfs
        "--cap-drop", "ALL", "--security-opt", "no-new-privileges",
        "--user", "65534:65534",
        "--pids-limit", "256", "--memory", memory, "--memory-swap", memory, "--cpus", "1",
        "--env", "HOME=/tmp", "--env", "PYTHONDONTWRITEBYTECODE=1",
        "-v", f"{checkout}:/src:ro",
        "-w", "/tmp",  # noqa: S108
        settings.sandbox_image, "sh", "-c", script,
    ]  # fmt: skip


def _docker_runner(argv: list[str], timeout: int) -> tuple[int | None, str]:
    try:
        done = subprocess.run(argv, capture_output=True, text=True, timeout=timeout, check=False)  # noqa: S603
    except subprocess.TimeoutExpired:
        name = argv[argv.index("--name") + 1]
        subprocess.run(["docker", "rm", "-f", name], capture_output=True, timeout=30, check=False)  # noqa: S603, S607
        return None, ""
    return done.returncode, done.stdout + done.stderr


def _run(checkout: Path, runner: Runner) -> SandboxRun:
    code, output = runner(
        build_docker_command(f"opspulse-sandbox-{uuid.uuid4().hex[:12]}", checkout), settings.sandbox_timeout_seconds
    )
    return SandboxRun(code, redact(output)[-OUTPUT_TAIL_CHARS:])


ENVIRONMENT_MARKERS = ("No module named", "command not found", ": not found", "executable file not found")


def _environment_problem(before: SandboxRun, after: SandboxRun) -> bool:
    """The same missing module or program shows up before AND after the patch: the image cannot run these tests, so
    neither run says anything about the patch. (A bare `python:3.11-slim` has no pytest: exit code 1, which would
    otherwise be reported as "tests fail both before and after".)"""
    return all(any(marker in run.output_tail for marker in ENVIRONMENT_MARKERS) for run in (before, after))


def classify(before: SandboxRun, after: SandboxRun) -> tuple[str, str]:
    if before.exit_code is None or after.exit_code is None:
        return "timeout", f"the tests did not finish within {settings.sandbox_timeout_seconds}s"
    if before.exit_code in DOCKER_ERROR_CODES or after.exit_code in DOCKER_ERROR_CODES:
        return "error", "the container could not run the tests (see the output; the image may lack the dependencies)"
    if _environment_problem(before, after):
        return (
            "error",
            "the image cannot run the tests: a module or program is missing both before and after the patch "
            "(use a SANDBOX_IMAGE that contains pytest and the repository's dependencies)",
        )
    if before.exit_code != 0 and after.exit_code == 0:
        return "fixes_failing_tests", "tests failed before the patch and pass after it"
    if before.exit_code == 0 and after.exit_code != 0:
        return "regression", "tests passed before the patch and fail after it"
    if before.exit_code == 0:
        return "no_regression", "tests pass both before and after the patch"
    return "still_failing", "tests fail both before and after the patch"


def run_patch_in_sandbox(
    repo_name: str,
    file_path: str,
    unified_diff: str,
    runner: Runner = _docker_runner,
    snapshot: Snapshot = download_repository_snapshot,
) -> SandboxReport:
    """Never raises: any failure becomes a report whose status is `error` or `unavailable`."""
    if runner is _docker_runner and shutil.which("docker") is None:
        return SandboxReport("unavailable", "Docker is not installed on the host running OpsPulse AI")
    limit = settings.sandbox_max_repo_mb * 1024 * 1024
    with tempfile.TemporaryDirectory(prefix="opspulse-sandbox-") as tmp:
        base, patched = Path(tmp) / "base", Path(tmp) / "patched"
        try:
            sha = snapshot(repo_name, base, limit)
            shutil.copytree(base, patched)
            target = patched / file_path
            if not target.resolve().is_relative_to(patched.resolve()) or not target.is_file():
                return SandboxReport("error", "the patched file is not in the repository snapshot", sha)
            target.write_text(apply_unified_diff(target.read_text(encoding="utf-8"), unified_diff), encoding="utf-8")
            before, after = _run(base, runner), _run(patched, runner)
        except (GitHubError, PatchError, OSError, ValueError) as exc:
            logger.warning("sandbox run failed: %s", type(exc).__name__)
            return SandboxReport("error", f"sandbox run failed: {redact(str(exc))[:200]}")
    status, summary = classify(before, after)
    return SandboxReport(status, summary, sha, before, after)
