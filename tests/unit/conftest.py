"""Unit-test environment: SQLite, no credentials, no network.

Every setting is pinned *before* `src` is imported so a developer's local `.env` (which python-dotenv does not
let override already-set variables) can never leak real credentials or endpoints into the unit tests.
"""

import hashlib
import os
import tempfile

_DB = os.path.join(tempfile.mkdtemp(prefix="opspulse-test-"), "test.db")
os.environ.update(
    {
        # TEST_DATABASE_URL lets CI run the same suite against a real PostgreSQL (tables are dropped per test!)
        "DATABASE_URL": os.environ.get("TEST_DATABASE_URL") or f"sqlite:///{_DB}",
        "NEON_DATABASE_URL": "",
        "GROQ_API_KEY": "",
        "MODEL_NAME": "test-model",
        "GITHUB_TOKEN": "",
        "LANGFUSE_PUBLIC_KEY": "",
        "LANGFUSE_SECRET_KEY": "",
        "API_KEY": "test-api-key",  # admin identity "default"
        "API_KEYS": ",".join(
            f"{name}:{role}:{hashlib.sha256(key.encode()).hexdigest()}"
            for name, role, key in (("alice", "reporter", "reporter-key"), ("bob", "reviewer", "reviewer-key"))
        ),
        "RATE_LIMIT_PER_MINUTE": "1000",
        "EMBEDDED_WORKER": "false",
        "SENTRY_CLIENT_SECRET": "sentry-test-secret",
        "SENTRY_PROJECT_REPOS": "1:o/r,2:other/repo",  # tests drive the queue explicitly with Worker().run_once()
        "ALLOW_UNAUTHENTICATED": "false",
        "ALLOWED_REPOSITORIES": "o/r",
        "ENABLE_GITHUB_REMEDIATION": "false",
        "SANDBOX_ENABLED": "false",  # a local `.env` with the sandbox on must not change what the unit tests exercise
        "SANDBOX_IMAGE": "python:3.11-slim",
        "QUALITY_THRESHOLD": "0.85",
        "MAX_ANALYSIS_ITERATIONS": "3",
        "MAX_PATCH_CHANGED_LINES": "40",
        "LOG_LEVEL": "WARNING",
    }
)

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from src.agent.graph import build_graph  # noqa: E402
from src.api.auth import ask_limiter, submission_limiter  # noqa: E402
from src.config import settings  # noqa: E402
from src.db import repositories  # noqa: E402
from src.db.client import get_engine  # noqa: E402
from src.db.models import Base  # noqa: E402
from src.main import app  # noqa: E402
from src.services import incident_service  # noqa: E402
from tests.unit.factories import SOURCE_CONTEXT, FakeLLM  # noqa: E402

API_HEADERS = {"X-API-Key": "test-api-key"}  # admin "default"
REPORTER_HEADERS = {"X-API-Key": "reporter-key"}  # alice
REVIEWER_HEADERS = {"X-API-Key": "reviewer-key"}  # bob


@pytest.fixture(autouse=True)
def fresh_limiter():
    submission_limiter.reset()
    ask_limiter.reset()
    yield


@pytest.fixture(autouse=True)
def fresh_db():
    Base.metadata.drop_all(get_engine())
    Base.metadata.create_all(get_engine())
    yield


@pytest.fixture
def set_settings():
    """Temporarily override fields of the frozen settings object; restored after the test."""
    original: dict = {}

    def _set(**values):
        for name, value in values.items():
            original.setdefault(name, getattr(settings, name))
            object.__setattr__(settings, name, value)

    yield _set
    for name, value in original.items():
        object.__setattr__(settings, name, value)


@pytest.fixture
def fake_llm(monkeypatch):
    """Install a FakeLLM: `fake_llm([reply1, reply2])` returns it for inspection."""

    def _install(replies):
        llm = FakeLLM(replies)
        monkeypatch.setattr("src.integrations.llm.get_chat_model", lambda temperature: llm)
        return llm

    return _install


@pytest.fixture
def source_fetch(monkeypatch):
    """Replace GitHub source retrieval in the application graph. Default: returns SOURCE_CONTEXT."""
    calls: list = []

    def _install(fetch=None):
        def default_fetch(repo, path, line):
            calls.append((repo, path, line))
            return SOURCE_CONTEXT

        graph = build_graph(fetch or default_fetch, repositories.find_similar_incidents)
        monkeypatch.setattr(incident_service, "get_graph", lambda: graph)
        return calls

    _install()
    return _install


@pytest.fixture
def client():
    return TestClient(app)
