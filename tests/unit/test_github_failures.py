"""GitHub failure paths: error classification, cleanup after a half-finished PR, plan fallbacks, odd targets."""

from types import SimpleNamespace

import pytest
from github import GithubException, RateLimitExceededException

from src.errors import ErrorCategory
from src.integrations import github
from src.integrations.github import GitHubError, api_error, create_fix_pull_request, fetch_source_context
from tests.unit.factories import GOOD_DIFF, SOURCE
from tests.unit.test_github import FakeRepo

ARGS = ("o/r", "src/app.py", GOOD_DIFF, "title", "body", "f" * 64)
BRANCH = "opspulse/fix-" + "f" * 16


class TrackingRepo(FakeRepo):
    """FakeRepo that can fail at chosen steps and remembers branch deletions."""

    def __init__(self, *args, fail_update=None, fail_pull=None, fail_label=False, **kwargs):
        super().__init__(*args, **kwargs)
        self.fail_update, self.fail_pull, self.fail_label = fail_update, fail_pull, fail_label
        self.deleted: list[str] = []
        self.labels_created: list[str] = []

    def update_file(self, **kwargs):
        if self.fail_update:
            raise GithubException(self.fail_update, {"message": "boom"}, None)
        super().update_file(**kwargs)

    def create_pull(self, **kwargs):
        if self.fail_pull and (kwargs["draft"] or self.fail_pull == "always"):
            raise GithubException(422 if kwargs["draft"] else 500, {"message": "no drafts"}, None)
        result = super().create_pull(**kwargs)
        if self.fail_label:
            result.add_to_labels = lambda *_: (_ for _ in ()).throw(GithubException(403, {"message": "x"}, None))
        return result

    def get_label(self, name):
        if self.fail_label:
            raise GithubException(404, {"message": "no label"}, None)
        return name

    def create_label(self, *args):
        raise GithubException(403, {"message": "cannot create labels"}, None)

    def get_git_ref(self, ref):
        return SimpleNamespace(delete=lambda: self.deleted.append(ref))


@pytest.fixture
def make_repo(monkeypatch):
    def _make(**kwargs):
        fake = TrackingRepo({"src/app.py": SOURCE}, **kwargs)
        monkeypatch.setattr(github, "get_repo", lambda name: fake)
        return fake

    return _make


# ------------------------------------------------------------------ classification


@pytest.mark.parametrize(
    "status,category",
    [
        (401, ErrorCategory.GITHUB_PERMISSION),
        (403, ErrorCategory.GITHUB_PERMISSION),
        (404, ErrorCategory.GITHUB_PERMISSION),
        (429, ErrorCategory.GITHUB_RATE_LIMITED),
        (500, ErrorCategory.GITHUB_UNAVAILABLE),
        (502, ErrorCategory.GITHUB_UNAVAILABLE),
    ],
)
def test_api_errors_are_categorised_and_never_carry_the_response_body(status, category):
    error = api_error(GithubException(status, {"message": "secret-looking body ghp_x"}, None), "ctx: ")
    assert error.category == category and "ghp_x" not in str(error) and str(error).startswith("ctx: ")


def test_primary_rate_limit_exception_is_a_rate_limit():
    exc = RateLimitExceededException(403, {"message": "API rate limit exceeded"}, None)
    assert api_error(exc).category == ErrorCategory.GITHUB_RATE_LIMITED


# ------------------------------------------------------------------ a half-finished PR is cleaned up


@pytest.mark.parametrize("failing_step", ["update", "pull"])
def test_failure_after_the_branch_was_created_removes_the_branch(make_repo, failing_step):
    repo = make_repo(fail_update=500) if failing_step == "update" else make_repo(fail_pull="always")
    with pytest.raises(GitHubError) as raised:
        create_fix_pull_request(*ARGS)
    assert raised.value.category == ErrorCategory.GITHUB_UNAVAILABLE
    assert repo.deleted == [f"heads/{BRANCH}"], "a stale branch would make every retry fail with 'already exists'"


def test_cleanup_failure_does_not_mask_the_original_error(make_repo):
    repo = make_repo(fail_update=500)
    repo.get_git_ref = lambda ref: (_ for _ in ()).throw(GithubException(403, {"message": "x"}, None))
    with pytest.raises(GitHubError) as raised:
        create_fix_pull_request(*ARGS)
    assert raised.value.category == ErrorCategory.GITHUB_UNAVAILABLE


def test_no_pr_and_no_branch_cleanup_when_the_failure_precedes_the_branch(make_repo):
    repo = make_repo()
    repo.files["src/app.py"] = SOURCE.replace("profile = data.get", "profile = payload.get")
    with pytest.raises(GitHubError):
        create_fix_pull_request(*ARGS)
    assert not repo.refs and not repo.deleted and not repo.pulls


# ------------------------------------------------------------------ plan and permission quirks


def test_draft_not_available_falls_back_to_a_regular_unmerged_pr(make_repo):
    repo = make_repo(fail_pull="draft")
    result = create_fix_pull_request(*ARGS)
    assert [p["draft"] for p in repo.pulls] == [False] and result.pr_number == 9
    assert not repo.deleted


def test_label_failures_do_not_lose_the_pull_request(make_repo):
    repo = make_repo(fail_label=True)
    result = create_fix_pull_request(*ARGS)
    assert result.pr_url.endswith("/pull/9") and len(repo.pulls) == 1 and not repo.deleted


def test_a_patch_with_no_effect_is_refused(make_repo, monkeypatch):
    repo = make_repo()
    monkeypatch.setattr(github, "apply_unified_diff", lambda original, diff: original)
    with pytest.raises(GitHubError, match="no change"):
        create_fix_pull_request(*ARGS)
    assert not repo.refs


def test_a_directory_target_is_refused(make_repo):
    repo = make_repo()
    repo.get_contents = lambda path, ref: [SimpleNamespace(path="src/app.py")]
    with pytest.raises(GitHubError, match="directory"):
        create_fix_pull_request(*ARGS)


# ------------------------------------------------------------------ opening the repository


def test_missing_token_is_a_skip_not_a_crash(set_settings):
    set_settings(github_token="", allowed_repositories=frozenset({"o/r"}))
    with pytest.raises(GitHubError) as raised:
        github.get_repo("o/r")
    assert raised.value.category == ErrorCategory.REMEDIATION_SKIPPED


def test_unknown_repository_is_a_permission_error(set_settings, monkeypatch):
    set_settings(github_token="t", allowed_repositories=frozenset({"o/r"}))

    class FakeClient:
        def __init__(self, **_):
            pass

        def get_repo(self, name):
            raise GithubException(404, {"message": "Not Found"}, None)

    monkeypatch.setattr(github, "Github", FakeClient)
    with pytest.raises(GitHubError, match="cannot open repository") as raised:
        github.get_repo("o/r")
    assert raised.value.category == ErrorCategory.GITHUB_PERMISSION


def test_fetch_treats_directories_and_empty_files_as_not_found(monkeypatch, set_settings):
    fake = TrackingRepo({"src/app.py": SOURCE})
    monkeypatch.setattr(github, "get_repo", lambda name: fake)
    fake.get_contents = lambda path, ref: [SimpleNamespace(path=path)]
    assert fetch_source_context("o/r", "/app/src/app.py", 3) is None
    empty = TrackingRepo({"src/app.py": ""})
    monkeypatch.setattr(github, "get_repo", lambda name: empty)
    assert fetch_source_context("o/r", "/app/src/app.py", 3) is None
