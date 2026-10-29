"""The history-based dataset builder, on a tiny real git repository created for the test."""

import pathlib
import subprocess
import sys

import pytest

from evals import history_real_repo as h
from evals.dataset import EvalCase

BUGGY = "def first(items):\n    return items[0]\n"
FIXED = "def first(items):\n    return items[0] if items else None\n"
TEST = "from pkg.mod import first\n\n\ndef test_first_of_empty():\n    assert first([]) is None\n"


def run(repo: pathlib.Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True)  # noqa: S603, S607


@pytest.fixture
def repo(tmp_path):
    root = tmp_path / "r"
    (root / "pkg").mkdir(parents=True)
    (root / "tests").mkdir()
    run(tmp_path, "init", "-q", "-b", "main", str(root))
    run(root, "config", "user.email", "t@example.com")
    run(root, "config", "user.name", "t")
    (root / "pkg" / "__init__.py").write_text("")
    (root / "pkg" / "mod.py").write_text(BUGGY)
    run(root, "add", ".")
    run(root, "commit", "-q", "-m", "Add first")
    (root / "pkg" / "mod.py").write_text(FIXED)
    (root / "tests" / "test_mod.py").write_text(TEST)
    run(root, "add", ".")
    run(root, "commit", "-q", "-m", "Fix crash on empty input")
    (root / "README.md").write_text("docs only\n")
    run(root, "add", ".")
    run(root, "commit", "-q", "-m", "Fix typo in docs")
    return root


def test_only_the_behaviour_fix_with_a_test_is_a_candidate(repo):
    found = h.find_candidates(repo, 10, "2000-01-01")
    assert [c.subject for c in found] == ["Fix crash on empty input"]
    assert found[0].source_files == ("pkg/mod.py",) and found[0].test_files == ("tests/test_mod.py",)


def test_a_real_red_to_green_commit_becomes_a_valid_case_and_patches_are_checked(repo):
    cand = h.find_candidates(repo, 10, "2000-01-01")[0]
    built = h.build_case(repo, sys.executable, cand, "o/r")
    assert not isinstance(built, str), built
    case, meta = built
    EvalCase.model_validate(case)  # the dataset loader would accept it
    assert "IndexError" in case["error_message"] and "/srv/pkg/mod.py" in case["stack_trace"]
    assert case["files"]["pkg/mod.py"] == BUGGY  # the code as it was BEFORE the fix, not the fixed code
    assert case["expected"]["relevant_files"] == ["pkg/mod.py"]

    good = (
        "--- a/pkg/mod.py\n+++ b/pkg/mod.py\n@@ -1,2 +1,2 @@\n def first(items):\n"
        "-    return items[0]\n+    return items[0] if items else None\n"
    )
    bad = (
        "--- a/pkg/mod.py\n+++ b/pkg/mod.py\n@@ -1,2 +1,2 @@\n def first(items):\n"
        "-    return items[0]\n+    return items[-1]\n"
    )
    assert h.verify_patch(repo, sys.executable, meta, good)[0] == "passes_tests"
    assert h.verify_patch(repo, sys.executable, meta, bad)[0] == "fails_tests"
    assert h.verify_patch(repo, sys.executable, meta, "not a diff")[0] in ("no_patch", "patch_error")


def test_a_commit_whose_test_never_fails_is_not_a_case(repo):
    (repo / "pkg" / "mod.py").write_text(FIXED + "# touched\n")
    (repo / "tests" / "test_mod.py").write_text("def test_nothing():\n    assert True\n")
    run(repo, "add", ".")
    run(repo, "commit", "-q", "-m", "Fix crash in nothing")
    cand = h.find_candidates(repo, 10, "2000-01-01")[0]  # newest first
    assert isinstance(h.build_case(repo, sys.executable, cand, "o/r"), str)


def test_frames_outside_the_repository_are_dropped_from_the_trace():
    trace = (
        "Traceback (most recent call last):\n"
        '  File "/venv/site-packages/_pytest/runner.py", line 1, in call\n    result = func()\n'
        '  File "/srv/tests/test_mod.py", line 4, in test_first\n    assert first([]) is None\n'
        '  File "/srv/pkg/mod.py", line 2, in first\n    return items[0]\n'
        "IndexError: list index out of range"
    )
    out = h.repo_frames_only(trace)
    assert "_pytest" not in out and "/venv/" not in out
    assert "/srv/pkg/mod.py" in out and "return items[0]" in out and out.endswith("IndexError: list index out of range")


def test_the_default_runner_is_an_isolated_container():
    cmd = h.docker_command("n", "img", pathlib.Path("/tmp/x"), ("t/test_a.py",))
    joined = " ".join(cmd)
    for flag in ("--network none", "--read-only", "--cap-drop ALL", "no-new-privileges", "--user 65534:65534"):
        assert flag in joined
    assert "--privileged" not in cmd and ":/src:ro" in joined and "t/test_a.py" in joined
    assert h.DEFAULT_RUNNER.startswith("docker:")
    assert h.trace_root(h.DEFAULT_RUNNER, pathlib.Path("/tmp/x")) == pathlib.Path("/tmp/w")
