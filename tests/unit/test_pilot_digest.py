"""The pilot digest: facts only, no recommendation, and it never prints the key."""

import urllib.error

from scripts import pilot_digest

PATCH = "--- a/x.py\n+++ b/x.py\n@@ -1,2 +1,3 @@\n-old\n+new\n+newer\n ctx\n"

INCIDENT = {
    "incident_id": "c1848f18-ed11-4554-969b-0488452e0619",
    "status": "awaiting_approval",
    "quality_score": 0.99,
    "iterations": 1,
    "affected_file": "discounts.py",
    "error_message": "RecursionError: maximum recursion depth exceeded\n  File ...",
    "suggested_patch": PATCH,
    "analysis": {
        "incident_summary": {"title": "Infinite recursion between two discount functions"},
        "root_cause_category": "logic_error",
        "uncertainties": ["a", "b"],
        "sandbox": {"status": "still_failing", "summary": "the same tests fail before and after the patch"},
    },
}


def fake(path):
    if path.startswith("/incidents?"):
        return {"items": [{"incident_id": INCIDENT["incident_id"]}]}
    return INCIDENT


def test_the_digest_states_the_facts_a_reviewer_reads_first():
    out = pilot_digest.digest(fake)
    assert "c1848f18" in out and "Infinite recursion between two discount functions" in out
    assert "RecursionError: maximum recursion depth exceeded" in out and "File ..." not in out  # first line only
    assert (
        "logic_error" in out
        and "patch +2 -1" in out
        and "still_failing" in out
        and "uncertainties the model listed: 2" in out
    )


def test_the_digest_gives_no_recommendation():
    out = pilot_digest.digest(fake).lower()
    for word in ("approve this", "should reject", "recommend", "looks good", "safe to"):
        assert word not in out


def test_an_empty_queue_and_a_missing_analysis_do_not_crash():
    assert "No incidents" in pilot_digest.digest(lambda path: {"items": []})
    bare = {"incident_id": "abcdef12-0000", "status": "needs_review"}
    out = pilot_digest.describe(bare)
    assert "no patch" in out and "not run" in out


def test_the_status_filter_is_sent_and_all_removes_it():
    seen = []
    pilot_digest.digest(lambda path: seen.append(path) or {"items": []}, "needs_review")
    pilot_digest.digest(lambda path: seen.append(path) or {"items": []}, "all")
    assert "status=needs_review" in seen[0] and "status=" not in seen[1]


def test_without_a_key_it_refuses_and_prints_no_traceback(monkeypatch, capsys):
    monkeypatch.delenv("OPSPULSE_API_KEY", raising=False)
    assert pilot_digest.main([]) == 2
    assert "OPSPULSE_API_KEY" in capsys.readouterr().err


def test_errors_are_one_line_and_never_contain_the_key(monkeypatch, capsys):
    monkeypatch.setenv("OPSPULSE_API_KEY", "SECRET-KEY-VALUE")

    def refused(*args, **kwargs):
        raise urllib.error.HTTPError("http://x", 403, "forbidden", {}, None)

    monkeypatch.setattr(pilot_digest, "_fetcher", lambda base, key: refused)
    assert pilot_digest.main([]) == 1
    captured = capsys.readouterr()
    assert "HTTP 403" in captured.err and "SECRET-KEY-VALUE" not in captured.out + captured.err

    def down(*args, **kwargs):
        raise urllib.error.URLError("connection refused")

    monkeypatch.setattr(pilot_digest, "_fetcher", lambda base, key: down)
    assert pilot_digest.main([]) == 1 and "is the API running" in capsys.readouterr().err
