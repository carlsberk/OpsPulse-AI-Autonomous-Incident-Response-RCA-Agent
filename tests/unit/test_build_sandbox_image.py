"""The sandbox image builder: which dependencies it reads, what it skips, and what it hands to `docker build`."""

from pathlib import Path

import pytest

from scripts import build_sandbox_image as b


def write(root: Path, name: str, text: str) -> None:
    path = root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)


def test_requirements_lines_are_cleaned_and_unsafe_forms_skipped():
    found, skipped = b.from_requirements_file(
        "# comment\nrequests>=2.31  # inline\nflask==3.0 \\\n    --hash=sha256:abc\n\n"
        "-e .\n-r other.txt\ngit+https://github.com/o/r.git\nmypkg @ https://x/y.whl\n./local\n"
        "numpy>=1.26; python_version >= '3.10'\n"
    )
    assert found == ["requests>=2.31", "flask==3.0", "numpy>=1.26; python_version >= '3.10'"]
    assert len(skipped) == 5


def test_pyproject_runtime_dependencies_and_test_extras_but_not_other_extras():
    deps = b.from_pyproject(
        '[project]\nname="x"\ndependencies=["httpx>=0.27"]\n'
        '[project.optional-dependencies]\ntest=["pytest-cov"]\ndocs=["mkdocs"]\n'
        '[dependency-groups]\ndev=["hypothesis", {include-group="test"}]\n'
    )
    assert deps == ["httpx>=0.27", "pytest-cov", "hypothesis"]


def test_collect_merges_files_dedupes_and_always_includes_pytest(tmp_path):
    write(tmp_path, "requirements.txt", "requests>=2.31\nPyYAML\n")
    write(tmp_path, "requirements/test.txt", "requests<3\nfreezegun\n-e .\n")
    write(tmp_path, "pyproject.toml", '[project]\nname="x"\ndependencies=["pyyaml>=6"]\n')
    deps, skipped, sources = b.collect(tmp_path)
    assert deps[0] == "pytest"
    assert [d.lower().split(">")[0].split("<")[0] for d in deps] == ["pytest", "requests", "pyyaml", "freezegun"]
    assert "requests>=2.31" in deps  # first declaration wins
    assert skipped == ["-e ."]
    assert sources == ["requirements.txt", "requirements/test.txt", "pyproject.toml"]


def test_an_empty_repository_still_gets_pytest_and_a_bad_pyproject_is_reported(tmp_path):
    assert b.collect(tmp_path) == (["pytest"], [], [])
    write(tmp_path, "pyproject.toml", "not = [valid")
    deps, skipped, _ = b.collect(tmp_path)
    assert deps == ["pytest"] and skipped == ["pyproject.toml (not valid TOML)"]


def test_an_unparsable_requirement_is_skipped_not_fatal(tmp_path):
    write(tmp_path, "requirements.txt", "good>=1\n!!bad!!\n")
    deps, skipped, _ = b.collect(tmp_path)
    assert "good>=1" in deps and skipped == ["!!bad!!"]


def test_the_dockerfile_installs_wheels_only_unless_source_builds_are_allowed():
    safe, loose = b.dockerfile("python:3.11-slim", True), b.dockerfile("python:3.11-slim", False)
    assert "--only-binary=:all:" in safe and "--only-binary" not in loose
    assert safe.startswith("FROM python:3.11-slim") and "PYTHONPATH=/tmp/work:/tmp/work/src" in safe


def test_the_default_tag_is_a_valid_image_name():
    assert b.default_tag("Owner/My_Repo.py") == "opspulse-sandbox-my_repo.py"
    assert b.default_tag("o/--") == "opspulse-sandbox"


def test_build_hands_a_context_with_both_files_to_docker(tmp_path):
    write(tmp_path / "repo", "requirements.txt", "requests>=2.31\n")
    seen = {}

    def fake_docker(argv):
        context = Path(argv[-1])
        seen["argv"] = argv[:-1]
        seen["deps"] = (context / "dependencies.txt").read_text()
        seen["dockerfile"] = (context / "Dockerfile").read_text()
        return 0

    assert b.build(tmp_path / "repo", "tag1", "python:3.11-slim", True, fake_docker) == 0
    assert seen["argv"] == ["docker", "build", "-t", "tag1"]
    assert seen["deps"] == "pytest\nrequests>=2.31\n" and "--only-binary" in seen["dockerfile"]


def test_main_dry_run_builds_nothing(tmp_path, capsys):
    write(tmp_path, "requirements.txt", "requests\n")
    calls = []
    code = b.main(["o/r", "--from-dir", str(tmp_path), "--dry-run"], run=lambda argv: calls.append(argv) or 0)
    out = capsys.readouterr().out
    assert code == 0 and not calls
    assert "2 packages: pytest, requests" in out and "FROM python:3.11-slim" in out


@pytest.mark.parametrize(("exit_code", "fragment"), [(0, "Set SANDBOX_IMAGE=opspulse-sandbox-r"), (1, "")])
def test_main_reports_the_build_result(tmp_path, capsys, exit_code, fragment):
    write(tmp_path, "requirements.txt", "requests\n")
    code = b.main(["o/r", "--from-dir", str(tmp_path)], run=lambda argv: exit_code)
    captured = capsys.readouterr()
    assert code == exit_code
    assert fragment in captured.out
    if exit_code:
        assert "--allow-source-builds" in captured.err


def test_main_says_so_when_the_download_fails(monkeypatch, capsys):
    def boom(repo, dest):
        raise RuntimeError("no token")

    monkeypatch.setattr(b, "_snapshot", boom)
    assert b.main(["o/r"]) == 2
    assert "cannot download o/r" in capsys.readouterr().err


def test_main_downloads_when_no_directory_is_given(monkeypatch, capsys):
    def fake(repo, dest):
        write(dest, "requirements.txt", "attrs\n")

    monkeypatch.setattr(b, "_snapshot", fake)
    assert b.main(["o/r", "--dry-run"]) == 0
    assert "2 packages: pytest, attrs" in capsys.readouterr().out
