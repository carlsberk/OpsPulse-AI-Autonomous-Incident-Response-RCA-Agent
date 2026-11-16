"""The real Docker runner's failure handling (the container is the isolation boundary, so a timeout must remove it),
and the sandbox's refusal to pretend when Docker is missing. No Docker is needed: `subprocess.run` is replaced."""

import subprocess
from types import SimpleNamespace

from src.integrations import sandbox


def test_a_timeout_force_removes_the_named_container_and_reports_no_exit_code(monkeypatch):
    calls = []

    def run(argv, **kwargs):
        calls.append(argv)
        if argv[0] != "docker" or "run" in argv:
            raise subprocess.TimeoutExpired(argv, 1)
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(sandbox.subprocess, "run", run)
    argv = ["docker", "run", "--rm", "--name", "opspulse-abc", "image"]
    assert sandbox._docker_runner(argv, timeout=1) == (None, "")
    assert calls[-1] == ["docker", "rm", "-f", "opspulse-abc"]  # the runaway container does not outlive the run


def test_stdout_and_stderr_are_both_captured_with_the_exit_code(monkeypatch):
    monkeypatch.setattr(
        sandbox.subprocess,
        "run",
        lambda argv, **kwargs: SimpleNamespace(returncode=1, stdout="out\n", stderr="err\n"),
    )
    assert sandbox._docker_runner(["docker", "run"], timeout=5) == (1, "out\nerr\n")


def test_without_docker_the_sandbox_says_unavailable_instead_of_guessing(monkeypatch):
    monkeypatch.setattr(sandbox.shutil, "which", lambda name: None)
    report = sandbox.run_patch_in_sandbox("o/r", "a.py", "--- a\n+++ b\n")
    assert report.status == "unavailable" and "Docker" in report.summary


def test_an_injected_runner_does_not_need_docker(monkeypatch):
    monkeypatch.setattr(sandbox.shutil, "which", lambda name: None)

    def broken_snapshot(repo, dest, max_bytes):
        raise sandbox.GitHubError("cannot fetch repository archive: X")

    report = sandbox.run_patch_in_sandbox(
        "o/r", "a.py", "--- a\n+++ b\n", runner=lambda argv, timeout: (0, ""), snapshot=broken_snapshot
    )
    assert report.status == "error"  # it got past the Docker check and failed on the snapshot, as a report not a raise
