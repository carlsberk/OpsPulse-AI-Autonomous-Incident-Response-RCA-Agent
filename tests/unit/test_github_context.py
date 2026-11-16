"""Source-context details: how the window is rendered, how repository paths are resolved, and what a pinned commit that
cannot be read does (it must fail loudly, not fall back to the default branch)."""

import io
import tarfile
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from github import GithubException

from src.integrations import github
from src.integrations.github import GitHubError, SourceContext, fetch_source_context
from tests.unit.factories import SOURCE
from tests.unit.test_github import FakeRepo


def _context(commits):
    return SourceContext("a.py", "c" * 12, 1, 3, 2, "1| x\n2| y", commits)


def test_the_rendered_context_lists_recent_commits_only_when_there_are_some():
    with_commits = _context(["abc1234 2026-11-01 fix typo"]).render()
    assert (
        "RECENT COMMITS TOUCHING THIS FILE (14 days):" in with_commits
        and "  - abc1234 2026-11-01 fix typo" in with_commits
    )
    without = _context([]).render()
    assert "RECENT COMMITS" not in without and without.startswith("FILE: a.py\nCOMMIT: " + "c" * 12)
    assert without.endswith("-----\n1| x\n2| y")


def test_recent_commits_are_summarised_as_sha_date_and_first_line(monkeypatch):
    commit = SimpleNamespace(
        sha="abcdef0123456789",
        commit=SimpleNamespace(
            author=SimpleNamespace(date=datetime(2026, 11, 1, tzinfo=UTC)),
            message="Fix the thing\n\nlong body that must not appear",
        ),
    )
    repo = SimpleNamespace(get_commits=lambda path, since: [commit])
    assert github._recent_commits(repo, "a.py") == ["abcdef0 2026-11-01 Fix the thing"]


@pytest.mark.parametrize("message", ["", "   \n", None])
def test_a_commit_with_an_empty_message_is_listed_with_an_empty_subject(message):
    # Regression: `"".splitlines()[0]` raised IndexError, which nothing catches, so one empty-message commit in the
    # last 14 days made the whole source-context fetch (and so the analysis) fail.
    commit = SimpleNamespace(
        sha="abcdef0123",
        commit=SimpleNamespace(author=SimpleNamespace(date=datetime(2026, 1, 2, tzinfo=UTC)), message=message),
    )
    repo = SimpleNamespace(get_commits=lambda path, since: [commit])
    assert github._recent_commits(repo, "a.py") == ["abcdef0 2026-01-02 "]


class _Pinned(FakeRepo):
    def __init__(self, *args, missing_commit=False, **kwargs):
        super().__init__(*args, **kwargs)
        self.missing_commit = missing_commit

    def get_commit(self, ref):
        if self.missing_commit:
            raise GithubException(404, {"message": "No commit found"}, None)
        return SimpleNamespace(sha="b" * 40)


def test_a_pinned_commit_is_the_one_read(monkeypatch):
    repo = _Pinned({"src/app.py": SOURCE})
    monkeypatch.setattr(github, "get_repo", lambda name: repo)
    context = fetch_source_context("o/r", "/app/src/app.py", 3, ref="b" * 40)
    assert context is not None and context.commit_sha == "b" * 12


def test_a_pinned_commit_that_cannot_be_read_is_an_error_not_a_fallback_to_the_head(monkeypatch):
    repo = _Pinned({"src/app.py": SOURCE}, missing_commit=True)
    monkeypatch.setattr(github, "get_repo", lambda name: repo)
    with pytest.raises(GitHubError, match="could not be read"):
        fetch_source_context("o/r", "/app/src/app.py", 3, ref="deadbeef" * 5)


@pytest.mark.parametrize("path", ["/app/../../etc/passwd", "..\\..\\secret.py", "src/../../x.py"])
def test_paths_with_parent_components_never_resolve(monkeypatch, path):
    repo = FakeRepo({"src/app.py": SOURCE})
    assert github.resolve_repo_path(repo, path, "main") is None


def test_an_ambiguous_suffix_picks_the_shortest_path_only_for_a_nested_candidate():
    repo = FakeRepo({"a/pkg/util.py": "x", "b/deep/pkg/util.py": "x"})
    assert github.resolve_repo_path(repo, "/srv/pkg/util.py", "main") == "a/pkg/util.py"


def test_a_bare_filename_with_several_matches_is_not_guessed():
    repo = FakeRepo({"a/util.py": "x", "b/util.py": "x"})
    assert github.resolve_repo_path(repo, "util.py", "main") is None


def _tar(path, entries):
    with tarfile.open(path, "w:gz") as tar:
        for name, kind, data in entries:
            info = tarfile.TarInfo(name)
            if kind == "dir":
                info.type = tarfile.DIRTYPE
                tar.addfile(info)
            elif kind == "fifo":
                info.type = tarfile.FIFOTYPE
                tar.addfile(info)
            else:
                info.size = len(data)
                tar.addfile(info, io.BytesIO(data))


def test_directories_are_created_and_special_files_are_skipped(tmp_path):
    archive = tmp_path / "a.tar.gz"
    _tar(archive, [("r-1/pkg", "dir", b""), ("r-1/pkg/m.py", "file", b"x=1"), ("r-1/pipe", "fifo", b"")])
    github._extract_regular_files(archive, tmp_path / "out", 1000)
    assert (tmp_path / "out" / "pkg" / "m.py").read_text() == "x=1"
    assert not (tmp_path / "out" / "pipe").exists()


def test_an_absolute_member_path_is_refused(tmp_path):
    archive = tmp_path / "abs.tar.gz"
    _tar(archive, [("/etc/cron.d/x", "file", b"x")])
    with pytest.raises(GitHubError, match="unsafe path"):
        github._extract_regular_files(archive, tmp_path / "out", 1000)
    assert not (tmp_path / "out" / "etc").exists()
