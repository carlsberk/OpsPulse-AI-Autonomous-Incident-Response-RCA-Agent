"""Supply-chain hygiene of the CI configuration, kept as tests so it cannot quietly regress."""

import pathlib
import re

import yaml

WORKFLOWS = sorted((pathlib.Path(__file__).resolve().parents[2] / ".github" / "workflows").glob("*.yml"))
SHA = re.compile(r"^[^@\s]+@[0-9a-f]{40}$")


def _steps(document):
    for job in (document.get("jobs") or {}).values():
        yield from job.get("steps") or []


def test_there_are_workflows():
    assert {p.name for p in WORKFLOWS} >= {"ci.yml", "security.yml", "release.yml", "docs.yml"}


def test_every_third_party_action_is_pinned_to_a_commit():
    unpinned = []
    for path in WORKFLOWS:
        for step in _steps(yaml.safe_load(path.read_text(encoding="utf-8"))):
            uses = step.get("uses")
            if uses and not uses.startswith("./") and not SHA.match(uses):
                unpinned.append(f"{path.name}: {uses}")
    assert not unpinned, "pin to a full commit SHA (keep the tag in a comment): " + ", ".join(unpinned)


def test_the_pin_comment_names_the_version():
    for path in WORKFLOWS:
        for line in path.read_text(encoding="utf-8").splitlines():
            if "uses:" in line and "@" in line and not line.strip().startswith("#"):
                assert re.search(r"@[0-9a-f]{40} # v\d+", line), f"{path.name}: {line.strip()}"


def test_workflows_declare_least_privilege_permissions():
    for path in WORKFLOWS:
        document = yaml.safe_load(path.read_text(encoding="utf-8"))
        assert "permissions" in document, f"{path.name} has no top-level permissions"
        assert document["permissions"].get("contents") in ("read", "write", None)


def test_test_workflows_do_not_keep_the_checkout_token():
    for name in ("ci.yml", "security.yml"):
        document = yaml.safe_load((WORKFLOWS[0].parent / name).read_text(encoding="utf-8"))
        for step in _steps(document):
            if str(step.get("uses", "")).startswith("actions/checkout@"):
                assert (step.get("with") or {}).get("persist-credentials") is False, name
