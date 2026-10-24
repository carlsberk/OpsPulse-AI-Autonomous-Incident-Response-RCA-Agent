"""GitHub integration against an in-memory fake of the PyGithub Repository API (no network)."""

from types import SimpleNamespace

import pytest
from github import GithubException

from src.integrations import github
from src.integrations.github import GitHubError, create_fix_pull_request, fetch_source_context
from tests.unit.factories import GOOD_DIFF, SOURCE


class FakeRepo:
    def __init__(self, files, open_prs=(), branch_exists=False):
        self.files = files
        self.open_prs = list(open_prs)
        self.branch_exists = branch_exists
        self.default_branch = "main"
        self.owner = SimpleNamespace(login="o")
        self.refs, self.updates, self.pulls = [], [], []

    def get_branch(self, name):
        return SimpleNamespace(commit=SimpleNamespace(sha="a" * 40))

    def get_git_tree(self, ref, recursive):
        return SimpleNamespace(tree=[SimpleNamespace(path=p, type="blob") for p in self.files])

    def get_contents(self, path, ref):
        if path not in self.files:
            raise GithubException(404, {"message": "Not Found"}, None)
        data = self.files[path].encode()
        return SimpleNamespace(path=path, size=len(data), decoded_content=data, sha="blobsha")

    def get_commits(self, path, since):
        raise GithubException(403, {"message": "secondary rate limit"}, None)

    def get_pulls(self, state, head):
        return [p for p in self.open_prs if p.head == head]

    def create_git_ref(self, ref, sha):
        if self.branch_exists:
            raise GithubException(422, {"message": "Reference already exists"}, None)
        self.refs.append(ref)

    def update_file(self, **kwargs):
        self.updates.append(kwargs)

    def create_pull(self, **kwargs):
        self.pulls.append(kwargs)
        return SimpleNamespace(html_url="https://github.com/o/r/pull/9", number=9, add_to_labels=lambda *_: None)

    def get_label(self, name):
        return name


@pytest.fixture
def repo(monkeypatch):
    fake = FakeRepo({"src/app.py": SOURCE, "README.md": "x"})
    monkeypatch.setattr(github, "get_repo", lambda name: fake)
    return fake


def test_fetch_resolves_runtime_path_and_numbers_lines(repo, set_settings):
    set_settings(context_radius=5)
    ctx = fetch_source_context("o/r", "src/app.py", 6)
    assert ctx.path == "src/app.py" and (ctx.start_line, ctx.end_line) == (1, 11)
    assert '     6 |     name = profile["name"]' in ctx.window_text
    assert ctx.recent_commits == []  # commit lookup failure is tolerated


def test_fetch_returns_none_for_unknown_file(repo):
    assert fetch_source_context("o/r", "lib/missing.py", 3) is None


def test_fetch_refuses_oversized_files(repo, monkeypatch):
    monkeypatch.setattr(github, "MAX_FILE_BYTES", 10)
    with pytest.raises(GitHubError, match="larger than"):
        fetch_source_context("o/r", "src/app.py", 6)


def test_repository_allow_list_is_enforced_before_any_api_call(set_settings):
    set_settings(github_token="test-token")
    with pytest.raises(GitHubError, match="ALLOWED_REPOSITORIES"):
        github.get_repo("someone/else")
    with pytest.raises(GitHubError, match="invalid repository"):
        github.get_repo("../etc")


def test_error_descriptions_never_include_response_bodies():
    exc = GithubException(401, {"message": "Bad credentials", "token": "ghp_" + "x" * 36}, None)
    assert github.describe_github_error(exc) == "GitHub rejected the token"


def test_pr_is_created_on_deterministic_branch_without_touching_default_branch(repo):
    result = create_fix_pull_request("o/r", "src/app.py", GOOD_DIFF, "title", "body", "f" * 64)
    assert result.branch == "opspulse/fix-" + "f" * 16 and not result.already_existed
    assert repo.refs == ["refs/heads/opspulse/fix-" + "f" * 16]
    assert repo.updates[0]["branch"] == result.branch != "main"
    assert 'raise ValueError("missing profile")' in repo.updates[0]["content"]
    assert repo.pulls[0]["base"] == "main" and repo.pulls[0]["draft"] is True
    assert "OpsPulse AI Automated Fix" in repo.pulls[0]["body"]
    assert "ffffffffffff" in repo.pulls[0]["body"]


def test_existing_open_pr_is_reused_not_duplicated(repo):
    branch = "opspulse/fix-" + "f" * 16
    repo.open_prs = [SimpleNamespace(head=f"o:{branch}", html_url="https://github.com/o/r/pull/3", number=3)]
    result = create_fix_pull_request("o/r", "src/app.py", GOOD_DIFF, "t", "b", "f" * 64)
    assert result.already_existed and result.pr_number == 3 and not repo.refs and not repo.pulls


def test_concurrent_run_holding_the_branch_is_detected(repo):
    repo.branch_exists = True
    with pytest.raises(GitHubError, match="already exists"):
        create_fix_pull_request("o/r", "src/app.py", GOOD_DIFF, "t", "b", "f" * 64)
    assert not repo.pulls


def test_patch_that_no_longer_applies_to_head_is_refused(repo):
    repo.files["src/app.py"] = SOURCE.replace("profile = data.get", "profile = payload.get")
    with pytest.raises(GitHubError, match="does not apply"):
        create_fix_pull_request("o/r", "src/app.py", GOOD_DIFF, "t", "b", "f" * 64)
    assert not repo.refs


def test_protected_paths_are_refused_before_api_calls(monkeypatch):
    monkeypatch.setattr(github, "get_repo", lambda name: pytest.fail("must not reach GitHub"))
    with pytest.raises(GitHubError, match="protected"):
        create_fix_pull_request("o/r", ".github/workflows/ci.yml", GOOD_DIFF, "t", "b", "f" * 64)
