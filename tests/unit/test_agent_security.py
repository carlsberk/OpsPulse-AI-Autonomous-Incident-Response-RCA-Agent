"""Agent security: what the agent can write, what a patch may contain, and what never reaches model, PR or logs."""

import ast
import pathlib
import re

import pytest

from src.logging_config import find_secrets, redact
from src.services import remediation_service
from src.services.remediation_service import PatchRejectedError, build_pull_request_body, validate_patch_for_pr
from tests.unit.factories import make_analysis

SRC = pathlib.Path(__file__).resolve().parents[2] / "src"

GITHUB_TOKEN = "ghp_" + "a1B2c3D4e5F6g7H8i9J0k1L2m3"
AWS_KEY = "AKIA" + "ABCDEFGHIJKLMNOP"
JWT = "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.abcdefghijklmnop"
PRIVATE_KEY = "-----BEGIN RSA PRIVATE KEY-----\nMIIEowIBAAKCAQEA\n-----END RSA PRIVATE KEY-----"


def _diff(added_line: str) -> str:
    return f"--- a/app.py\n+++ b/app.py\n@@ -1,1 +1,2 @@\n x = 1\n+{added_line}\n"


# ------------------------------------------------------------------ secrets


@pytest.mark.parametrize("secret", [GITHUB_TOKEN, AWS_KEY, JWT, PRIVATE_KEY, "xoxb-1234567890-abcdefghij"])
def test_redact_masks_every_known_credential_shape(secret):
    assert secret.split("\n")[1 if "PRIVATE" in secret else 0][:12] not in redact(f"failed with {secret} at line 3")


@pytest.mark.parametrize(
    "added",
    [f'TOKEN = "{GITHUB_TOKEN}"', f"aws = '{AWS_KEY}'", 'password = "hunter2hunter2"', f"jwt = '{JWT}'"],
)
def test_a_patch_that_adds_a_credential_is_rejected_without_echoing_it(added):
    with pytest.raises(PatchRejectedError) as raised:
        validate_patch_for_pr(_diff(added), "app.py", 50)
    message = str(raised.value)
    assert "credential" in message
    for secret in (GITHUB_TOKEN, AWS_KEY, "hunter2hunter2", JWT):
        assert secret not in message


def test_a_patch_that_only_removes_or_mentions_secrets_is_not_blocked():
    removed = f"--- a/app.py\n+++ b/app.py\n@@ -1,2 +1,1 @@\n x = 1\n-TOKEN = '{GITHUB_TOKEN}'\n"
    validate_patch_for_pr(removed, "app.py", 50)  # removing a leaked secret is a good change
    validate_patch_for_pr(_diff("token = os.environ['API_TOKEN']"), "app.py", 50)


def test_model_text_in_the_pull_request_body_is_redacted():
    analysis = make_analysis()
    analysis["patch_remediation"]["explanation"] = f"set the header to Bearer {'x' * 24} and {GITHUB_TOKEN}"
    fix = remediation_service.ProposedFix(
        incident_id="00000000-0000-0000-0000-000000000001",
        repo_name="o/r",
        fingerprint="f",
        target_file="app.py",
        patch=_diff("y = 2"),
        analysis=analysis,
        quality_score=0.9,
        iterations=1,
    )
    body = build_pull_request_body(fix)
    assert GITHUB_TOKEN not in body and "x" * 24 not in body


def test_credentials_in_a_reported_error_never_reach_the_model(fake_llm):
    from tests.unit.test_graph import TRACE, run_graph

    llm = fake_llm([make_analysis()])
    run_graph(trace=TRACE + f"\nrequests.get(url, headers={{'Authorization': 'Bearer {GITHUB_TOKEN}'}})")
    assert GITHUB_TOKEN not in llm.prompts[0]


# ------------------------------------------------------------------ what the agent can write


def _attribute_calls(path: pathlib.Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    return {n.func.attr for n in ast.walk(tree) if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)}


def test_github_writes_exist_only_in_the_github_adapter_and_are_reachable_only_from_remediation():
    write_calls = {
        "create_git_ref",
        "update_file",
        "create_file",
        "create_pull",
        "create_label",
        "merge",
        "delete_file",
    }
    offenders = {}
    for path in SRC.rglob("*.py"):
        used = _attribute_calls(path) & write_calls
        if used and path.name != "github.py":
            offenders[str(path.relative_to(SRC))] = sorted(used)
    assert not offenders, f"GitHub write calls outside the adapter: {offenders}"

    adapter = _attribute_calls(SRC / "integrations" / "github.py")
    assert "merge" not in adapter and "delete_file" not in adapter  # merging is never an agent action

    importers = [
        str(p.relative_to(SRC))
        for p in SRC.rglob("*.py")
        if re.search(r"\bcreate_fix_pull_request\b", p.read_text(encoding="utf-8")) and p.name != "github.py"
    ]
    assert importers == ["services\\remediation_service.py"] or importers == ["services/remediation_service.py"]


def test_the_model_client_is_given_no_tools():
    source = (SRC / "integrations" / "llm.py").read_text(encoding="utf-8")
    assert "bind_tools" not in source and "tools=" not in source


def test_secret_finder_reports_kinds_not_values():
    kinds = find_secrets(f"a = '{GITHUB_TOKEN}'; b = '{AWS_KEY}'")
    assert kinds == ["github_token", "aws_key_id"]
