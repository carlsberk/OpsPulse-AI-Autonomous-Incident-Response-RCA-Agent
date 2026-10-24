"""The pilot readiness check: it names what blocks a pilot and never prints a secret."""

from types import SimpleNamespace

import pytest
from sqlalchemy import text

from scripts import pilot_check
from scripts.pilot_check import FAIL, NOTE, OK, WARN
from src.config import ApiIdentity
from src.db.client import get_engine

SECRET_TOKEN = "ghp_SECRETSECRETSECRETSECRETSECRET123456"
SECRET_GROQ = "gsk_VERYSECRETGROQKEY"
SECRET_DB = "postgresql://user:hunter2@db.example/app"


def _cfg(**overrides):
    base = {
        "groq_api_key": SECRET_GROQ,
        "database_url": SECRET_DB,
        "api_identities": (
            ApiIdentity("alertmanager", "reporter", "a" * 64),
            ApiIdentity("maria", "reviewer", "b" * 64),
        ),
        "allow_self_approval": False,
        "allowed_repositories": frozenset({"me/pilot"}),
        "enable_github_remediation": True,
        "require_remediation_approval": True,
        "github_pr_draft": True,
        "github_token": SECRET_TOKEN,
        "sandbox_enabled": False,
        "sandbox_image": "python:3.11-slim",
        "history_reviewer_notes": True,
        "notify_webhook_url": "https://hooks.example/x",
    }
    base.update(overrides)
    return SimpleNamespace(**base)


def _levels(findings):
    return {f.name: f.level for f in findings}


def test_a_ready_configuration_has_no_blocker():
    findings = pilot_check.configuration(_cfg())
    assert FAIL not in {f.level for f in findings} and WARN not in {f.level for f in findings}
    assert "Ready for a pilot." in pilot_check.render(findings)


@pytest.mark.parametrize(
    ("override", "name"),
    [
        ({"groq_api_key": ""}, "GROQ_API_KEY"),
        ({"database_url": ""}, "DATABASE_URL"),
        ({"api_identities": ()}, "API_KEYS"),
        ({"api_identities": (ApiIdentity("alice", "reporter", "a" * 64),)}, "API_KEYS"),  # nobody can decide
        ({"allowed_repositories": frozenset()}, "ALLOWED_REPOSITORIES"),
        ({"enable_github_remediation": False}, "ENABLE_GITHUB_REMEDIATION"),
        ({"require_remediation_approval": False}, "REQUIRE_REMEDIATION_APPROVAL"),
        ({"github_token": ""}, "GITHUB_TOKEN"),
    ],
)
def test_blockers(override, name):
    findings = pilot_check.configuration(_cfg(**override))
    assert any(f.name == name and f.level == FAIL for f in findings)
    assert "Not ready" in pilot_check.render(findings)


@pytest.mark.parametrize(
    ("override", "name"),
    [
        ({"allow_self_approval": True}, "ALLOW_SELF_APPROVAL"),
        ({"github_pr_draft": False}, "GITHUB_PR_DRAFT"),
        ({"database_url": "sqlite:///x.db"}, "DATABASE_URL"),
        ({"github_token": "not-a-github-token"}, "GITHUB_TOKEN"),
        ({"api_identities": (ApiIdentity("maria", "reviewer", "b" * 64),)}, "API_KEYS"),  # one key, no reporter
    ],
)
def test_warnings_do_not_block(override, name):
    findings = pilot_check.configuration(_cfg(**override))
    assert any(f.name == name and f.level == WARN for f in findings)
    assert FAIL not in {f.level for f in findings}


def test_notes_say_what_is_off():
    levels = _levels(pilot_check.configuration(_cfg(notify_webhook_url="")))
    assert levels["SANDBOX_ENABLED"] == NOTE and levels["NOTIFY_WEBHOOK_URL"] == NOTE


def test_no_secret_is_ever_printed():
    cfg = _cfg(
        api_identities=(ApiIdentity("alertmanager", "reporter", "c" * 64), ApiIdentity("maria", "reviewer", "d" * 64))
    )
    findings = pilot_check.configuration(cfg) + pilot_check.configuration(_cfg(github_token="oops-" + SECRET_TOKEN))
    output = pilot_check.render(findings)
    for secret in (SECRET_TOKEN, SECRET_GROQ, SECRET_DB, "hunter2", "c" * 64, "d" * 64):
        assert secret not in output


# ------------------------------------------------------------------ database


@pytest.fixture
def no_version_table():
    """The test database may already carry an `alembic_version` table (the PostgreSQL CI job migrates it before the
    tests run); the models' `drop_all` does not touch it, so these tests start from a known state."""

    def drop():
        with get_engine().begin() as connection:
            connection.execute(text("DROP TABLE IF EXISTS alembic_version"))

    drop()
    yield
    drop()


def test_unmigrated_database_blocks_with_the_fix(no_version_table):
    (finding,) = pilot_check.database()
    assert finding.level == FAIL and "alembic upgrade head" in finding.message


def test_migrated_database_is_ok(no_version_table):
    from alembic.config import Config
    from alembic.script import ScriptDirectory

    cfg = Config("alembic.ini")
    cfg.set_main_option("script_location", "migrations")
    head = ScriptDirectory.from_config(cfg).get_current_head()
    with get_engine().begin() as connection:
        connection.execute(text("CREATE TABLE alembic_version (version_num VARCHAR(32) NOT NULL)"))
        connection.execute(text("INSERT INTO alembic_version VALUES (:v)"), {"v": head})
    (finding,) = pilot_check.database()
    assert finding.level == OK and head in finding.message


def test_unreachable_database_is_a_finding_not_a_traceback(monkeypatch):
    from sqlalchemy.exc import OperationalError

    def broken():
        raise OperationalError("SELECT 1", {}, Exception(f"password {SECRET_DB}"))

    monkeypatch.setattr("src.db.client.ping_database", broken)
    (finding,) = pilot_check.database()
    assert finding.level == FAIL and "cannot connect" in finding.message and "hunter2" not in finding.message


# ------------------------------------------------------------------ GitHub and Docker (faked)


def _repo(**kw):
    values = {"private": True, "archived": False, "default_branch": "main", "permissions": SimpleNamespace(push=True)}
    values.update(kw)
    return SimpleNamespace(**values)


def test_github_ok_and_notes_public():
    ok = pilot_check.github(_cfg(), lambda name: _repo())
    assert ok[0].level == OK and "private" in ok[0].message and "token can push" in ok[0].message
    public = pilot_check.github(_cfg(), lambda name: _repo(private=False))
    assert [f.level for f in public] == [OK, NOTE]


def test_github_token_that_cannot_push_blocks():
    (finding,) = pilot_check.github(_cfg(), lambda name: _repo(permissions=SimpleNamespace(push=False)))
    assert finding.level == FAIL and "cannot push" in finding.message


def test_github_archived_blocks():
    (finding,) = pilot_check.github(_cfg(), lambda name: _repo(archived=True))
    assert finding.level == FAIL and "archived" in finding.message


def test_github_error_reports_the_category_only():
    def boom(name):
        error = RuntimeError("401 Bad credentials " + SECRET_TOKEN)
        error.category = "github_permission"
        raise error

    (finding,) = pilot_check.github(_cfg(), boom)
    assert finding.level == FAIL and "github_permission" in finding.message and SECRET_TOKEN not in finding.message


def test_docker_is_skipped_when_the_sandbox_is_off():
    assert pilot_check.docker(_cfg(), run=lambda argv: 1) == []


@pytest.mark.parametrize(
    ("returncodes", "expected", "fragment"),
    [([0, 0], OK, "present"), ([1], FAIL, "no Docker daemon"), ([0, 1], FAIL, "not present locally")],
)
def test_docker_checks(returncodes, expected, fragment):
    codes = iter(returncodes)
    (finding,) = pilot_check.docker(_cfg(sandbox_enabled=True), run=lambda argv: next(codes))
    assert finding.level == expected and fragment in finding.message


# ------------------------------------------------------------------ command line


def test_main_exit_code_follows_blockers(monkeypatch, capsys):
    monkeypatch.setattr("src.config.settings", _cfg(github_token=""), raising=False)
    monkeypatch.setattr(pilot_check, "database", lambda: [pilot_check.Finding(OK, "database", "fine")])
    assert pilot_check.main([]) == 1
    out = capsys.readouterr().out
    assert "[FAIL] GITHUB_TOKEN" in out and SECRET_GROQ not in out
    monkeypatch.setattr("src.config.settings", _cfg(), raising=False)
    assert pilot_check.main([]) == 0
