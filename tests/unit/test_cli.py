"""The command-line client, exercised against the real application through an in-process transport."""

import json

import pytest

from src.cli import ApiError, call, main
from tests.unit.factories import ERROR, TRACE, make_analysis

BASE = "http://opspulse.test"


@pytest.fixture
def cli(client, capsys):
    """Run the CLI against the app; returns (exit code, stdout, stderr)."""

    def transport(method, url, headers, body):
        resp = client.request(method, url.removeprefix(BASE), headers=headers, content=body)
        return resp.status_code, resp.content

    def run(*argv, key="test-api-key"):
        code = main(["--url", BASE, "--key", key, *argv], transport=transport)
        captured = capsys.readouterr()
        return code, captured.out, captured.err

    return run


def _submit(cli, fake_llm, tmp_path, *extra):
    fake_llm([make_analysis()])
    trace = tmp_path / "trace.txt"
    trace.write_text(TRACE, encoding="utf-8")
    return cli("submit", "--repo", "o/r", "--error", ERROR, "--trace-file", str(trace), *extra)


def test_submit_wait_then_show_guidance_and_ask(cli, fake_llm, source_fetch, tmp_path):
    code, out, _ = _submit(cli, fake_llm, tmp_path, "--wait")
    assert code == 0 and "status" in out and "gate score" in out and "observed evidence" in out
    incident_id = next(line.split()[-1] for line in out.splitlines() if line.startswith("incident"))

    code, out, _ = cli("show", incident_id, "--patch")
    assert code == 0 and "--- a/src/app.py" in out  # the proposed patch is printed on request

    code, out, _ = cli("guidance", incident_id)
    assert code == 0 and out.startswith("[") and "gate score" in out

    code, out, _ = cli("ask", incident_id, "What should I do next?")
    assert code == 0 and "[rules]" in out and "not executed or verified" in out

    code, out, _ = cli("list")
    assert code == 0 and incident_id in out


def test_json_flag_prints_the_raw_response(cli, fake_llm, source_fetch, tmp_path):
    code, out, _ = _submit(cli, fake_llm, tmp_path)
    assert code == 0 and out.startswith("accepted:")
    code, out, _ = cli("--json", "health")
    assert code == 0 and json.loads(out)["healthz"]["status"] == "ok"


def test_api_errors_are_reported_with_exit_code_1(cli):
    code, _, err = cli("list", key="wrong-key")
    assert code == 1 and "401" in err
    code, _, err = cli("show", "00000000-0000-0000-0000-000000000000")
    assert code == 1 and "404" in err


def test_usage_and_connection_problems_exit_with_2(client, capsys):
    def refused(method, url, headers, body):
        raise ConnectionRefusedError("connection refused")

    assert main(["--url", BASE, "list"], transport=refused) == 2
    assert "connection refused" in capsys.readouterr().err
    assert main(["--url", BASE, "submit", "--repo", "o/r"], transport=refused) == 2  # no error text given


def test_there_is_deliberately_no_approve_command():
    with pytest.raises(SystemExit):
        main(["approve", "x"])


def test_call_raises_api_error_with_the_server_message():
    def denied(method, url, headers, body):
        return 403, b'{"error": {"message": "role not allowed"}}'

    with pytest.raises(ApiError) as info:
        call(denied, BASE, "k", "GET", "/incidents")
    assert info.value.status == 403 and "role not allowed" in str(info.value)
