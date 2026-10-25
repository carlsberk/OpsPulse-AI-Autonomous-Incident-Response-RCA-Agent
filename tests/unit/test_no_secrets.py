"""No credential-shaped string is committed, except the fake ones that tests use on purpose."""

import pathlib
import re
import subprocess

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]

# Provider token shapes. Only the first characters of a hit are ever shown in a failure message.
PATTERNS = {
    "Groq key": re.compile(r"gsk_[A-Za-z0-9]{20,}"),
    "GitHub token": re.compile(r"(?:ghp|gho|ghu|ghs|ghr)_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{30,}"),
    "AWS access key": re.compile(r"AKIA[0-9A-Z]{16}"),
    "Slack token": re.compile(r"xox[abprs]-[A-Za-z0-9-]{10,}"),
    "OpenAI-style key": re.compile(r"\bsk-[A-Za-z0-9]{32,}"),
    "Langfuse key": re.compile(r"\b[sp]k-lf-[0-9a-f-]{20,}"),
    "private key": re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
    "database URL with a password": re.compile(
        r"postgres(?:ql)?(?:\+\w+)?://[^:/\s@]+:(?!PASSWORD|password|\*|\$|ci-only|opspulse-local-only)[^@\s/]{4,}@"
    ),
}

# Files that hold fake credentials on purpose (redaction and refusal tests). Add to this list only for the same reason.
FIXTURES = {
    "tests/unit/test_agent_security.py",
    "tests/unit/test_api.py",
    "tests/unit/test_no_secrets.py",  # the scanner's own planted examples
    "tests/unit/test_observability.py",
    "tests/unit/test_pilot_check.py",
}


def _tracked_files():
    try:
        out = subprocess.run(["git", "ls-files", "-z"], cwd=ROOT, capture_output=True, check=True).stdout  # noqa: S607
    except (OSError, subprocess.CalledProcessError):
        pytest.skip("not a git checkout")
    return [ROOT / name for name in out.decode().split("\0") if name]


def test_env_files_are_not_tracked():
    tracked = {p.relative_to(ROOT).as_posix() for p in _tracked_files()}
    assert not {name for name in tracked if name == ".env" or name.endswith("/.env")}


def test_no_credential_shaped_strings_outside_the_fixtures():
    hits = []
    for path in _tracked_files():
        relative = path.relative_to(ROOT).as_posix()
        if relative in FIXTURES or path.suffix in {".png", ".jpg", ".svg", ".lock"} or not path.is_file():
            continue
        if path.stat().st_size > 2_000_000:
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        for number, line in enumerate(text.splitlines(), start=1):
            for label, pattern in PATTERNS.items():
                match = pattern.search(line)
                if match:
                    hits.append(f"{relative}:{number} looks like a {label} ({match.group(0)[:6]}...)")
    assert not hits, "commit no credentials; if this is a deliberate fake, put it in a test fixture:\n" + "\n".join(
        hits
    )


def test_the_scanner_itself_catches_a_planted_secret():
    planted = "token = 'ghp_" + "A" * 36 + "'"
    assert PATTERNS["GitHub token"].search(planted)
    assert PATTERNS["private key"].search("-----BEGIN OPENSSH PRIVATE KEY-----")
    assert not PATTERNS["database URL with a password"].search("postgresql://USER:PASSWORD@HOST/DB")
    assert PATTERNS["database URL with a password"].search("postgresql://app:hunter22@db.example/app")


def test_the_scanner_does_not_exempt_files_by_accident():
    """Every exempt file must exist and really hold a fake; a stale entry would silently widen the exemption."""
    tracked = {p.relative_to(ROOT).as_posix() for p in _tracked_files()}
    missing = sorted(name for name in FIXTURES if name not in tracked)
    assert not missing, f"remove from FIXTURES (not tracked): {missing}"
    for name in sorted(FIXTURES):
        text = (ROOT / name).read_text(encoding="utf-8")
        assert any(pattern.search(text) for pattern in PATTERNS.values()), (
            f"{name} holds no fake credential; drop it from FIXTURES"
        )
