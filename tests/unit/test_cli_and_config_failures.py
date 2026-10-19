"""CLI exit codes for failures, and configuration parsing that must fail loudly rather than run misconfigured."""

import hashlib

import pytest

from src import config
from src.cli import call, main
from src.config import ConfigError


def _fixed(status, body=b""):
    return lambda method, url, headers, payload: (status, body)


# ------------------------------------------------------------------ CLI


def test_server_errors_exit_1_and_show_the_server_message(capsys):
    code = main(
        ["--url", "http://x", "show", "abc"],
        transport=_fixed(404, b'{"error": {"code": "not_found", "message": "incident not found"}}'),
    )
    assert code == 1 and "404" in capsys.readouterr().err


def test_a_non_json_error_body_still_exits_1(capsys):
    assert main(["--url", "http://x", "health"], transport=_fixed(502, b"<html>bad gateway</html>")) == 1
    assert "502" in capsys.readouterr().err


def test_connection_problems_exit_2_with_a_message(capsys):
    def refused(*_args):
        raise ConnectionRefusedError("connection refused")

    assert main(["--url", "http://x", "health"], transport=refused) == 2
    assert "connection refused" in capsys.readouterr().err


def test_submitting_without_an_error_message_is_a_usage_error(capsys):
    assert main(["--url", "http://x", "submit", "--repo", "o/r", "--error", "  "], transport=_fixed(202)) == 2
    assert "--error" in capsys.readouterr().err


def test_a_missing_file_is_a_usage_error_not_a_traceback(capsys):
    code = main(
        ["--url", "http://x", "submit", "--repo", "o/r", "--error-file", "/no/such/file"], transport=_fixed(202)
    )
    assert code == 2 and "Traceback" not in capsys.readouterr().err


def test_the_default_transport_refuses_non_http_urls(capsys):
    assert main(["--url", "file:///etc/passwd", "health"]) == 2
    assert "http" in capsys.readouterr().err


def test_the_api_key_is_sent_as_a_header_and_never_in_the_url():
    seen = {}

    def transport(method, url, headers, payload):
        seen.update(url=url, headers=headers)
        return 200, b"{}"

    call(transport, "http://x/", "s3cret", "GET", "/healthz")
    assert seen["headers"]["X-API-Key"] == "s3cret" and "s3cret" not in seen["url"]


# ------------------------------------------------------------------ configuration


@pytest.mark.parametrize("value", ["maybe", "2", "tru"])
def test_booleans_must_be_boolean(monkeypatch, value):
    monkeypatch.setenv("SOME_FLAG", value)
    with pytest.raises(ConfigError, match="boolean"):
        config._bool("SOME_FLAG", False)


@pytest.mark.parametrize("value,expected", [("TRUE", True), ("off", False), ("1", True), ("no", False)])
def test_boolean_spellings(monkeypatch, value, expected):
    monkeypatch.setenv("SOME_FLAG", value)
    assert config._bool("SOME_FLAG", not expected) is expected


def test_blank_booleans_use_the_default(monkeypatch):
    monkeypatch.setenv("SOME_FLAG", "  ")
    assert config._bool("SOME_FLAG", True) is True


def test_numbers_are_validated_and_bounded(monkeypatch):
    monkeypatch.setenv("SOME_NUMBER", "abc")
    with pytest.raises(ConfigError, match="must be a int"):
        config._number(("SOME_NUMBER",), 5, int, 1, 10)
    monkeypatch.setenv("SOME_NUMBER", "99")
    with pytest.raises(ConfigError, match="between"):
        config._number(("SOME_NUMBER",), 5, int, 1, 10)
    monkeypatch.setenv("SOME_NUMBER", "7")
    assert config._number(("SOME_NUMBER",), 5, int, 1, 10) == 7
    monkeypatch.delenv("SOME_NUMBER")
    assert config._number(("SOME_NUMBER",), 5, int, 1, 10) == 5


def test_repository_lists_are_trimmed_lowercased_and_deduplicated(monkeypatch):
    monkeypatch.setenv("SOME_REPOS", " Org/Repo , org/repo,, other/Thing ")
    assert config._repo_list("SOME_REPOS") == frozenset({"org/repo", "other/thing"})


@pytest.mark.parametrize(
    "entry",
    ["onlyname", "alice:superuser:" + "a" * 64, "alice:reporter:notahash", ":reporter:" + "a" * 64, "a:b:c:d"],
)
def test_malformed_api_key_entries_stop_startup(monkeypatch, entry):
    monkeypatch.setenv("API_KEYS", entry)
    with pytest.raises(ConfigError, match="API_KEYS"):
        config._identities()


def test_well_formed_api_key_entries_are_accepted(monkeypatch):
    digest = hashlib.sha256(b"k").hexdigest()
    monkeypatch.setenv("API_KEYS", f"alice:reporter:{digest}, bob:reviewer:{digest}")
    monkeypatch.delenv("API_KEY", raising=False)
    assert [i.name for i in config._identities()] == ["alice", "bob"]
