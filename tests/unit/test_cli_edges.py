"""CLI pieces the end-to-end CLI tests do not reach: the real transport's rules, stdin input, and output formats."""

import io
import json
import urllib.error

import pytest

from src import cli


class _Response:
    status = 200

    def __init__(self, body=b"{}"):
        self._body = body

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def read(self):
        return self._body


def test_the_transport_refuses_non_http_schemes_before_any_request(monkeypatch):
    monkeypatch.setattr(cli.urllib.request, "urlopen", lambda *a, **k: pytest.fail("must not connect"))
    for url in ("file:///etc/passwd", "ftp://host/x", "//host/x"):
        with pytest.raises(ValueError, match="http:// or https://"):
            cli._urllib_transport("GET", url, {}, None)


def test_the_transport_returns_status_and_body_and_passes_the_key_header(monkeypatch):
    seen = {}

    def urlopen(request, timeout):
        seen["headers"], seen["method"], seen["timeout"] = dict(request.header_items()), request.method, timeout
        return _Response(b'{"ok": true}')

    monkeypatch.setattr(cli.urllib.request, "urlopen", urlopen)
    status, body = cli._urllib_transport("POST", "http://x/y", {"X-API-Key": "k"}, b"{}")
    assert (status, body) == (200, b'{"ok": true}')
    assert seen["headers"]["X-api-key"] == "k" and seen["method"] == "POST" and seen["timeout"] == 120


def test_an_http_error_status_is_returned_not_raised_so_the_api_message_can_be_shown(monkeypatch):
    def urlopen(request, timeout):
        raise urllib.error.HTTPError("http://x", 409, "conflict", {}, io.BytesIO(b'{"detail": "nope"}'))

    monkeypatch.setattr(cli.urllib.request, "urlopen", urlopen)
    assert cli._urllib_transport("GET", "http://x", {}, None) == (409, b'{"detail": "nope"}')


def test_input_can_come_from_stdin_a_file_or_nothing(monkeypatch, tmp_path):
    monkeypatch.setattr(cli.sys, "stdin", io.StringIO("from stdin"))
    assert cli._read_text("-") == "from stdin"
    path = tmp_path / "e.txt"
    path.write_text("from file", encoding="utf-8")
    assert cli._read_text(str(path)) == "from file" and cli._read_text(None) == ""


def test_a_missing_input_file_is_exit_code_2_with_one_line(capsys, tmp_path):
    code = cli.main(
        ["--url", "http://x", "--key", "k", "submit", "--repo", "o/r", "--error-file", str(tmp_path / "no")]
    )
    assert code == 2 and capsys.readouterr().err.startswith("error:")


def test_an_empty_error_is_refused_before_contacting_the_api(capsys):
    def transport(*args):
        pytest.fail("must not call the API")

    code = cli.main(
        ["--url", "http://x", "--key", "k", "submit", "--repo", "o/r", "--error", "  "], transport=transport
    )
    assert code == 2 and "--error" in capsys.readouterr().err


def test_an_api_error_is_exit_code_1_and_names_the_status(capsys):
    code = cli.main(
        ["--url", "http://x", "--key", "k", "show", "abc"],
        transport=lambda *a: (404, json.dumps({"detail": "incident not found"}).encode()),
    )
    assert code == 1 and "404" in capsys.readouterr().err


def test_the_answer_tags_say_when_the_answer_cannot_be_trusted():
    text = cli.format_answer({"answer": "a", "source": "model", "degraded": True, "answerable": False})
    assert "model unavailable" in text and "not in the record" in text
    ungrounded = cli.format_answer({"answer": "a", "source": "model", "answerable": True, "grounded": False})
    assert "NOT GROUNDED" in ungrounded
    cited = cli.format_answer({"answer": "a", "source": "m", "cited_sections": ["logs", "code"]})
    assert "sections: logs, code" in cited


def test_guidance_lists_steps_blocking_checks_and_facts():
    text = cli.format_guidance(
        {
            "state": "needs_review",
            "headline": "Check this",
            "explanation": "why",
            "next_steps": [{"audience": "reviewer", "text": "read the patch"}],
            "failed_checks": [{"name": "grounding", "blocking": True, "advice": "cite more"}],
            "facts": ["gate 0.62"],
        }
    )
    assert "[needs_review] Check this" in text and "1. (reviewer) read the patch" in text
    assert "grounding [blocking]: cite more" in text and ". gate 0.62" in text


def test_an_incident_without_an_analysis_still_formats_and_shows_the_patch_only_on_request():
    incident = {
        "incident_id": "i",
        "status": "awaiting_approval",
        "suggested_patch": "--- a\n+++ b\n",
        "pending_approval": True,
    }
    assert "--- a" not in cli.format_incident(incident)
    shown = cli.format_incident(incident, show_patch=True)
    assert "--- a" in shown and "waiting for a reviewer" in shown and "awaiting approval" in shown


def test_the_demo_command_hands_over_to_the_demo_script(monkeypatch):
    from scripts import demo_console

    monkeypatch.setattr(demo_console, "main", lambda argv: 7)
    assert cli.main(["demo"]) == 7
