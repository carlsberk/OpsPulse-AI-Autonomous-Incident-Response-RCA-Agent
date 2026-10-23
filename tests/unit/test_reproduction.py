"""The model's regression test: validated as text, run only in the sandbox, and read as evidence, not as proof."""

from pathlib import Path

import pytest

from src.agent import reproduction as repro
from src.agent.reproduction import ReproductionSpec, expected_error_name
from src.agent.schemas import RCAOutput
from src.integrations.sandbox import (
    SandboxRun,
    classify_reproduction,
    run_patch_in_sandbox,
)
from src.services.remediation_service import ProposedFix, build_pull_request_body
from tests.unit.factories import GOOD_DIFF, SOURCE, make_analysis

GOOD_TEST = "from src.app import load_user\n\n\ndef test_reproduces_the_incident():\n    load_user({})\n"


def ran(code, text=""):
    return SandboxRun(code, text)


# ------------------------------------------------------------------ validating text


@pytest.mark.parametrize(
    "path,ok",
    [
        ("tests/test_regression_incident.py", True),
        ("test_incident.py", True),
        ("tests/incident_test.py", True),
        ("src/app.py", False),  # not a test file name
        ("../tests/test_x.py", False),
        ("/tests/test_x.py", False),
        ("tests//test_x.py", False),
        ("a/b/c/d/e/test_x.py", False),  # too deep
        ("tests/test x.py", False),
        ("tests/test_x.txt", False),
        ("", False),
    ],
)
def test_the_test_path_must_be_a_new_looking_relative_test_file(path, ok):
    assert (repro.validate_path(path) is None) is ok


@pytest.mark.parametrize(
    "content,fragment",
    [
        (GOOD_TEST, None),
        ("def test_x(:\n", "not valid Python"),
        ("x = 1\n", "no test function"),
        ("import subprocess\n\ndef test_x():\n    pass\n", "subprocess"),
        ("from urllib.request import urlopen\n\ndef test_x():\n    pass\n", "urllib"),
        ("def test_x():\n    open('/etc/passwd')\n", "open()"),
        ("def test_x():\n    __import__('os').system('id')\n", "()"),  # either forbidden call is reported
        ("import os\n\ndef test_x():\n    os.system('id')\n", ".system()"),
        ("def test_x():\n    eval('1')\n", "eval()"),
        ("", "between 1 and"),
        ("def test_x():\n    pass\n" + "# pad\n" * 2000, "between 1 and"),
    ],
)
def test_the_test_text_is_checked_statically(content, fragment):
    problem = repro.validate_content(content)
    assert (problem is None) if fragment is None else (problem is not None and fragment in problem)


@pytest.mark.parametrize(
    "message,name",
    [
        ("TypeError: 'NoneType' object is not subscriptable", "TypeError"),
        ("httpx.PoolTimeout", "PoolTimeout"),
        ("  KeyError: 'name'", "KeyError"),
        ("ZeroDivisionError: division by zero\nsecond line", "ZeroDivisionError"),
        ("500 Internal Server Error at /checkout", None),
        ("", None),
        ("the checkout failed", None),
    ],
)
def test_the_incidents_exception_class_is_read_from_its_first_line(message, name):
    assert expected_error_name(message) == name


# ------------------------------------------------------------------ the schema never loses the analysis to it


def test_a_valid_reproduction_test_is_kept_and_a_malformed_one_becomes_none_without_failing_the_analysis():
    analysis = make_analysis()
    analysis["reproduction_test"] = {"path": "tests/test_x.py", "content": GOOD_TEST}
    assert RCAOutput.model_validate(analysis).reproduction_test.path == "tests/test_x.py"
    for malformed in ("a string", 5, {"path": 1, "content": "x"}, {"path": "t.py"}, {"path": "", "content": "x"}, []):
        analysis["reproduction_test"] = malformed
        assert RCAOutput.model_validate(analysis).reproduction_test is None
    analysis.pop("reproduction_test")
    assert RCAOutput.model_validate(analysis).reproduction_test is None


# ------------------------------------------------------------------ what a pair of runs means


@pytest.mark.parametrize(
    "before,after,expected,status",
    [
        (ran(1, "E   TypeError: x"), ran(0, "1 passed"), "TypeError", "reproduced_and_fixed"),
        (ran(1, "E   TypeError: x"), ran(1, "E   TypeError: x"), "TypeError", "reproduced_not_fixed"),
        (ran(0, "1 passed"), ran(0, "1 passed"), "TypeError", "not_reproduced"),
        (ran(1, "E   ImportError: no"), ran(0, "1 passed"), "TypeError", "fails_for_another_reason"),
        (ran(1, "SyntaxError"), ran(0, "1 passed"), "TypeError", "fails_for_another_reason"),
        (ran(1, "E   TypeError: x"), ran(0, "1 passed"), None, "fails_for_another_reason"),
        (ran(1, "No module named pytest"), ran(1, "No module named pytest"), "TypeError", "error"),
        (ran(125, ""), ran(125, ""), "TypeError", "error"),
        (ran(None, ""), ran(0, ""), "TypeError", "timeout"),
    ],
)
def test_classifying_the_reproduction(before, after, expected, status):
    assert classify_reproduction(before, after, expected)[0] == status


def test_a_test_that_fails_for_a_different_reason_is_never_called_reproduced():
    """A typo or a wrong import makes a test fail before AND (differently) after; that must not look like success."""
    status, summary = classify_reproduction(ran(1, "NameError: name 'x'"), ran(0, "ok"), "TypeError")
    assert status != "reproduced_and_fixed" and "TypeError" in summary


# ------------------------------------------------------------------ in the sandbox (fake runner)


def fake_snapshot(repo, dest: Path, limit):
    (dest / "src").mkdir(parents=True)
    (dest / "src" / "app.py").write_text(SOURCE, encoding="utf-8")
    return "abc123"


def make_runner(outputs):
    """Answers by what was asked: the full suite, or one test file (the argv names it)."""
    calls = []

    def runner(argv, timeout):
        script = argv[-1]
        checkout = Path(next(a for a in argv if a.endswith(":/src:ro")).removesuffix(":/src:ro"))
        calls.append((checkout.name, script, (checkout / "tests" / "test_repro.py").exists()))
        kind = "repro" if "test_repro.py" in script else "suite"
        return outputs[(kind, checkout.name)]

    runner.calls = calls
    return runner


SPEC = ReproductionSpec("tests/test_repro.py", GOOD_TEST, "TypeError")
SUITE_OK = {("suite", "base"): (0, "3 passed"), ("suite", "patched"): (0, "3 passed")}


def test_the_reproduction_runs_only_the_new_file_on_both_checkouts(set_settings):
    runner = make_runner(
        {**SUITE_OK, ("repro", "base"): (1, "E   TypeError: x"), ("repro", "patched"): (0, "1 passed")}
    )
    report = run_patch_in_sandbox("o/r", "src/app.py", GOOD_DIFF, runner, fake_snapshot, SPEC)
    assert report.reproduction.status == "reproduced_and_fixed"
    suite = [c for c in runner.calls if "test_repro.py" not in c[1]]
    single = [c for c in runner.calls if "test_repro.py" in c[1]]
    assert len(suite) == 2 and len(single) == 2
    assert all(not has_file for _, _, has_file in suite), "the repository's own tests must not see the model's test"
    assert all(has_file for _, _, has_file in single)
    assert "tests/test_repro.py" in single[0][1]


def test_no_reproduction_test_means_exactly_the_old_behaviour(set_settings):
    runner = make_runner(SUITE_OK)
    report = run_patch_in_sandbox("o/r", "src/app.py", GOOD_DIFF, runner, fake_snapshot)
    assert report.reproduction is None and len(runner.calls) == 2


def test_the_reproduction_is_skipped_when_the_sandbox_itself_cannot_run(set_settings):
    runner = make_runner({("suite", "base"): (125, ""), ("suite", "patched"): (125, "")})
    report = run_patch_in_sandbox("o/r", "src/app.py", GOOD_DIFF, runner, fake_snapshot, SPEC)
    assert report.status == "error" and report.reproduction is None and len(runner.calls) == 2


@pytest.mark.parametrize(
    "spec",
    [
        ReproductionSpec("tests/test_repro.py", "import subprocess\n\ndef test_x():\n    pass\n", "TypeError"),
        ReproductionSpec("../escape/test_repro.py", GOOD_TEST, "TypeError"),
        ReproductionSpec("src/app.py", GOOD_TEST, "TypeError"),  # an existing, non-test file: never overwritten
    ],
)
def test_an_invalid_test_is_reported_and_never_run(set_settings, spec):
    runner = make_runner(SUITE_OK)
    report = run_patch_in_sandbox("o/r", "src/app.py", GOOD_DIFF, runner, fake_snapshot, spec)
    assert report.reproduction.status == "invalid" and len(runner.calls) == 2


def test_an_existing_test_file_is_never_overwritten(set_settings):
    def snapshot(repo, dest, limit):
        fake_snapshot(repo, dest, limit)
        (dest / "tests").mkdir()
        (dest / "tests" / "test_repro.py").write_text("def test_theirs():\n    pass\n", encoding="utf-8")
        return "abc123"

    runner = make_runner(SUITE_OK)
    report = run_patch_in_sandbox("o/r", "src/app.py", GOOD_DIFF, runner, snapshot, SPEC)
    assert report.reproduction.status == "invalid" and "new file" in report.reproduction.summary


# ------------------------------------------------------------------ what the reviewer reads


def _body(status):
    analysis = make_analysis()
    if status:
        analysis["sandbox"] = {"status": "no_regression", "summary": "ok", "reproduction": {"status": status}}
    fix = ProposedFix("00000000-0000-0000-0000-000000000001", "o/r", "f", "src/app.py", "diff", analysis, 0.9, 1)
    return build_pull_request_body(fix)


def test_the_pull_request_text_reports_the_reproduction_honestly():
    fixed = _body("reproduced_and_fixed")
    assert "Reproduction test (written by the model, run in the sandbox, not part of this PR)" in fixed
    assert "reproduced the incident's exception before the patch and passed after it" in fixed
    assert "The model wrote both the test and the fix: read the test too." in fixed
    assert "did not reproduce the incident" in _body("not_reproduced")
    assert "not with the incident's exception" in _body("fails_for_another_reason")
    for quiet in (_body(None), _body("invalid"), _body("error"), _body("timeout")):
        assert "Reproduction test" not in quiet


# ------------------------------------------------------------------ "reproduced" needs the exception to END the test


@pytest.mark.parametrize(
    "name,output,raised",
    [
        ("KeyError", "E   KeyError: 'findings'", True),
        ("KeyError", "E   builtins.KeyError: 'x'", True),
        ("TypeError", "app/x.py:31: TypeError", True),
        ("PoolTimeout", "E   httpx.PoolTimeout", True),
        ("KeyError", "E   AttributeError: boom\n    execution.output['findings']  # a KeyError in the source", False),
        ("KeyError", "mentions KeyError in prose", False),
        ("KeyError", "E   KeyErrorish: no", False),
        ("KeyError", "", False),
    ],
)
def test_only_the_exception_that_ends_the_test_counts_as_the_one_it_raised(name, output, raised):
    from src.integrations.sandbox import _raised

    assert _raised(name, output) is raised


def test_a_test_that_dies_of_its_own_bug_while_the_incidents_name_appears_in_the_output_is_not_a_reproduction():
    """Seen with the live model: the test built an object wrongly (AttributeError), and the word KeyError appeared in
    the quoted source line; a substring match called that a reproduction that the patch then 'failed to fix'."""
    own_bug = "app/x.py:12: in evaluate\n    execution.output['findings']  # KeyError\nE   AttributeError: no attribute"
    assert classify_reproduction(ran(1, own_bug), ran(1, own_bug), "KeyError")[0] == "fails_for_another_reason"
