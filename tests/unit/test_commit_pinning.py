"""Analysis pinned to the deployed commit: the code that failed may differ from the default branch head."""

from types import SimpleNamespace

import pytest
from github import GithubException

from src.agent.graph import build_graph
from src.agent.state import initial_state
from src.integrations import github
from src.integrations.alertmanager import to_incidents
from src.integrations.github import GitHubError, fetch_source_context
from src.integrations.revision import normalize_commit_sha
from src.integrations.sentry import to_incident
from tests.unit.conftest import API_HEADERS, REPORTER_HEADERS
from tests.unit.factories import ERROR, SOURCE, SOURCE_CONTEXT, TRACE, incident_payload, make_analysis
from tests.unit.test_alertmanager import BEARER, NOTIFICATION
from tests.unit.test_github import FakeRepo

SHA = "0123456789abcdef0123456789abcdef01234567"
HEAD_SHA = "a" * 40


class PinnedRepo(FakeRepo):
    """Knows two commits: the default-branch head and one older, deployed commit."""

    def __init__(self, files):
        super().__init__(files)
        self.commits_asked: list[str] = []

    def get_commit(self, ref):
        self.commits_asked.append(ref)
        if SHA.startswith(ref.lower()):
            return SimpleNamespace(sha=SHA)
        raise GithubException(422, {"message": "No commit found for the ref"}, None)


@pytest.fixture
def pinned_repo(monkeypatch):
    repo = PinnedRepo({"src/app.py": SOURCE})
    monkeypatch.setattr(github, "get_repo", lambda name: repo)
    return repo


# ------------------------------------------------------------------ reading source at a commit


def test_source_is_read_at_the_deployed_commit_not_the_head(pinned_repo, set_settings):
    context = fetch_source_context("o/r", "/app/src/app.py", 6, ref=SHA[:12])
    assert context.commit_sha == SHA[:12] and pinned_repo.commits_asked == [SHA[:12]]
    assert f"COMMIT: {SHA[:12]}" in context.render()


def test_without_a_pin_the_head_is_used_as_before(pinned_repo):
    context = fetch_source_context("o/r", "/app/src/app.py", 6)
    assert context.commit_sha == HEAD_SHA[:12] and pinned_repo.commits_asked == []


def test_an_unreadable_commit_is_an_error_never_a_silent_fallback_to_the_head(pinned_repo):
    with pytest.raises(GitHubError, match="commit deadbeefcafe could not be read"):
        fetch_source_context("o/r", "/app/src/app.py", 6, ref="deadbeefcafe")


# ------------------------------------------------------------------ the workflow


def _run(commit_sha, fetch):
    graph = build_graph(fetch, lambda *a: [])
    state = initial_state("00000000-0000-0000-0000-000000000001", ERROR, TRACE, "o/r", commit_sha)
    return graph.invoke(state, config={"recursion_limit": 25})


def test_the_pin_reaches_the_fetcher_only_when_there_is_one(fake_llm):
    fake_llm([make_analysis()] * 3)
    calls = []

    def fetch(repo, path, line, **kwargs):
        calls.append(kwargs)
        return SOURCE_CONTEXT

    _run(SHA, fetch)
    _run(None, fetch)
    assert calls and calls[0] == {"ref": SHA} and calls[-1] == {}
    assert all(call == {"ref": SHA} for call in calls[: len(calls) // 2])


def test_old_style_three_argument_fetchers_keep_working_without_a_pin(fake_llm):
    fake_llm([make_analysis()])
    assert _run(None, lambda repo, path, line: SOURCE_CONTEXT)["context_status"] == "retrieved"


def test_an_unreadable_deployed_commit_ends_in_review_with_the_reason_not_in_a_guess(fake_llm):
    llm = fake_llm([make_analysis()] * 3)

    def fetch(repo, path, line, ref=None):
        raise GitHubError(f"commit {ref[:12]} could not be read: GitHub API error 422")

    final = _run(SHA, fetch)
    assert final["context_status"] == "unavailable" and "could not be read" in final["context_note"]
    assert final["workflow_status"] == "needs_review" and final["error_category"] == "source_unavailable"
    assert llm.calls <= 1


def test_a_file_missing_at_the_commit_says_so(fake_llm):
    fake_llm([make_analysis()] * 3)
    final = _run(SHA, lambda repo, path, line, ref=None: None)
    assert final["context_status"] == "not_found" and f"commit {SHA[:12]}" in final["context_note"]


# ------------------------------------------------------------------ intake


@pytest.mark.parametrize("raw", [SHA, SHA.upper(), "abc1234", "  " + SHA[:12] + " "])
def test_valid_shas_are_normalised(raw):
    assert normalize_commit_sha(raw) == raw.strip().lower()


@pytest.mark.parametrize("raw", ["1.4.2", "v2026.10", "main", "abc12", "g" * 12, SHA + "0", "", None, 42, ["abc1234"]])
def test_version_strings_and_other_values_are_not_shas(raw):
    assert normalize_commit_sha(raw) is None


def test_the_api_stores_and_returns_the_commit_and_rejects_nonsense(client, fake_llm, source_fetch):
    fake_llm([make_analysis()])
    ok = client.post("/webhook/incident?wait=true", json=incident_payload(commit_sha=SHA), headers=API_HEADERS)
    assert ok.status_code == 200 and ok.json()["commit_sha"] == SHA
    stored = client.get(f"/incidents/{ok.json()['incident_id']}", headers=REPORTER_HEADERS).json()
    assert stored["commit_sha"] == SHA
    bad = client.post("/webhook/incident", json=incident_payload(commit_sha="main"), headers=API_HEADERS)
    assert bad.status_code == 422


def test_a_queued_incident_keeps_its_pin_until_the_worker_runs_it(client, fake_llm, monkeypatch):
    """The worker reads the pin from the row: it was not in the request that the worker never sees."""
    from src import worker as worker_module
    from src.services import incident_service

    seen = {}
    real = incident_service.run_incident_pipeline

    def spy(*args, **kwargs):
        seen["commit_sha"] = kwargs.get("commit_sha")
        return real(*args, **kwargs)

    monkeypatch.setattr(incident_service, "run_incident_pipeline", spy)
    fake_llm([make_analysis()])
    graph = build_graph(lambda repo, path, line, ref=None: SOURCE_CONTEXT, lambda *a: [])
    monkeypatch.setattr(incident_service, "get_graph", lambda: graph)
    response = client.post("/webhook/incident", json=incident_payload(commit_sha=SHA), headers=API_HEADERS)
    assert response.status_code == 202
    assert worker_module.Worker().run_once() is True
    assert seen["commit_sha"] == SHA


def test_alertmanager_takes_the_commit_from_a_label_or_an_annotation():
    labelled = {**NOTIFICATION, "alerts": [dict(NOTIFICATION["alerts"][0])]}
    labelled["alerts"][0] = {**labelled["alerts"][0], "labels": {**labelled["alerts"][0]["labels"], "commit_sha": SHA}}
    annotated = {**NOTIFICATION, "alerts": [dict(NOTIFICATION["alerts"][0])]}
    annotated["alerts"][0] = {
        **annotated["alerts"][0],
        "annotations": {**annotated["alerts"][0]["annotations"], "commit_sha": SHA[:10]},
    }
    assert to_incidents(labelled, "repository")[0][0].commit_sha == SHA
    assert to_incidents(annotated, "repository")[0][0].commit_sha == SHA[:10]
    assert to_incidents(NOTIFICATION, "repository")[0][0].commit_sha is None
    assert BEARER  # the receiver is exercised end to end in test_alertmanager


def _sentry_event(release):
    return {
        "data": {
            "event": {
                "event_id": "e1",
                "project": "1",
                "release": release,
                "exception": {"values": [{"type": "KeyError", "value": "'x'"}]},
            }
        }
    }


def test_sentry_uses_the_release_only_when_it_is_a_git_sha():
    assert to_incident(_sentry_event(SHA)).commit_sha == SHA
    assert to_incident(_sentry_event("my-app@1.4.2")).commit_sha is None
    assert to_incident(_sentry_event(None)).commit_sha is None
