"""PR labelling is best-effort, and repository archives are untrusted input (path traversal, links, size)."""

import io
import tarfile
from types import SimpleNamespace

import pytest
from github import GithubException

from src.integrations import github
from src.integrations.github import GitHubError, create_fix_pull_request
from tests.unit.factories import GOOD_DIFF, SOURCE
from tests.unit.test_github import FakeRepo


class LabellingRepo(FakeRepo):
    """FakeRepo where neither label exists yet, so both have to be created."""

    def __init__(self, *args, fail_labels=False, **kwargs):
        super().__init__(*args, **kwargs)
        self.created_labels = []
        self.fail_labels = fail_labels

    def get_label(self, name):
        raise GithubException(404, {"message": "Not Found"}, None)

    def create_label(self, name, color, description):
        if self.fail_labels:
            raise GithubException(403, {"message": "forbidden"}, None)
        self.created_labels.append(name)


@pytest.fixture
def labelling_repo(monkeypatch):
    def _make(**kwargs):
        fake = LabellingRepo({"src/app.py": SOURCE}, **kwargs)
        monkeypatch.setattr(github, "get_repo", lambda name: fake)
        return fake

    return _make


def test_missing_labels_are_created_and_attached(labelling_repo):
    repo = labelling_repo()
    create_fix_pull_request("o/r", "src/app.py", GOOD_DIFF, "t", "b", "a" * 64)
    assert repo.created_labels == [github.PR_LABEL, github.REVIEW_LABEL]
    assert repo.pulls[0]["labels"] == [github.PR_LABEL, github.REVIEW_LABEL]


def test_a_labelling_failure_never_turns_an_opened_pr_into_an_error(labelling_repo):
    repo = labelling_repo(fail_labels=True)
    result = create_fix_pull_request("o/r", "src/app.py", GOOD_DIFF, "t", "b", "a" * 64)
    assert result.pr_number == 9 and len(repo.pulls) == 1


def test_pr_body_ends_with_the_footer_and_the_short_fingerprint(labelling_repo):
    repo = labelling_repo()
    create_fix_pull_request("o/r", "src/app.py", GOOD_DIFF, "t", "  body  ", "abcdef0123456789" * 4)
    body = repo.pulls[0]["body"]
    assert body.startswith("body\n\n---") and "`abcdef012345`" in body
    assert body.rstrip().endswith("before merging.*")


def _tar(path, members):
    with tarfile.open(path, "w:gz") as tar:
        for info, data in members:
            tar.addfile(info, io.BytesIO(data) if data is not None else None)
    return path


def _file(name, data=b"x"):
    info = tarfile.TarInfo(name)
    info.size = len(data)
    return info, data


def test_archive_extraction_drops_the_top_level_prefix(tmp_path):
    archive = _tar(tmp_path / "a.tar.gz", [_file("o-r-sha/src/app.py", b"print(1)")])
    dest = tmp_path / "out"
    github._extract_regular_files(archive, dest, max_bytes=1000)
    assert (dest / "src" / "app.py").read_bytes() == b"print(1)"


def test_archive_with_a_parent_directory_path_is_rejected(tmp_path):
    archive = _tar(tmp_path / "a.tar.gz", [_file("o-r-sha/../evil.txt")])
    with pytest.raises(GitHubError, match="unsafe path"):
        github._extract_regular_files(archive, tmp_path / "out", max_bytes=1000)
    assert not (tmp_path / "evil.txt").exists()


def test_archive_with_an_absolute_path_is_rejected(tmp_path):
    archive = _tar(tmp_path / "a.tar.gz", [_file("/etc/evil.txt")])
    with pytest.raises(GitHubError, match="unsafe path"):
        github._extract_regular_files(archive, tmp_path / "out", max_bytes=1000)


def test_symlinks_in_an_archive_are_never_extracted(tmp_path):
    link = tarfile.TarInfo("o-r-sha/link")
    link.type = tarfile.SYMTYPE
    link.linkname = "/etc/passwd"
    archive = _tar(tmp_path / "a.tar.gz", [(link, None), _file("o-r-sha/ok.txt")])
    dest = tmp_path / "out"
    github._extract_regular_files(archive, dest, max_bytes=1000)
    assert (dest / "ok.txt").exists() and not (dest / "link").exists() and not (dest / "link").is_symlink()


def test_extracted_size_is_capped(tmp_path):
    archive = _tar(tmp_path / "a.tar.gz", [_file("o-r-sha/big.bin", b"x" * 100)])
    with pytest.raises(GitHubError, match="size limit"):
        github._extract_regular_files(archive, tmp_path / "out", max_bytes=50)


def test_snapshot_download_is_capped_and_cleans_up_the_archive(tmp_path, monkeypatch):
    class Repo:
        default_branch = "main"

        def get_branch(self, name):
            return SimpleNamespace(commit=SimpleNamespace(sha="a" * 40))

        def get_archive_link(self, kind, ref):
            return "https://example.invalid/archive"

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def raise_for_status(self):
            return None

        def iter_content(self, size):
            yield b"x" * 200

    monkeypatch.setattr(github, "get_repo", lambda name: Repo())
    monkeypatch.setattr(github.requests, "get", lambda *a, **k: Response())
    dest = tmp_path / "snap"
    with pytest.raises(GitHubError, match="exceeds the sandbox size limit"):
        github.download_repository_snapshot("o/r", dest, max_bytes=100)
    assert not (tmp_path / "snap.tar.gz").exists()
