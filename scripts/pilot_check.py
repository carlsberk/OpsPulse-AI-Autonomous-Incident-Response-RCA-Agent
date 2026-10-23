"""Is this installation ready for a first real pilot? Reads the configuration and says what is missing.

    python -m scripts.pilot_check             # configuration and database only, nothing leaves this machine
    python -m scripts.pilot_check --online    # also ask GitHub about the allowed repositories, and Docker if on

It never prints a secret or any part of one: findings name the setting, not its value. The online mode makes read-only
GitHub calls with the configured token (repository metadata only). Exit code 1 when something blocks the pilot, 0 when
only warnings or notes remain. The checklist it follows is in docs/RUNBOOK.md ("Running a real pilot").
"""

from __future__ import annotations

import argparse
import subprocess
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

OK, WARN, FAIL, NOTE = "ok", "warn", "fail", "note"
_TOKEN_PREFIXES = ("ghp_", "github_pat_", "gho_", "ghu_", "ghs_")


@dataclass(frozen=True)
class Finding:
    level: str
    name: str
    message: str


def configuration(cfg: Any) -> list[Finding]:
    """Checks that need nothing but the settings."""
    found: list[Finding] = []

    def add(level: str, name: str, message: str) -> None:
        found.append(Finding(level, name, message))

    add(OK, "GROQ_API_KEY", "set") if cfg.groq_api_key else add(
        FAIL, "GROQ_API_KEY", "not set: no live analysis is possible"
    )
    if not cfg.database_url:
        add(FAIL, "DATABASE_URL", "not set")
    elif cfg.database_url.startswith("sqlite"):
        add(
            WARN,
            "DATABASE_URL",
            "SQLite is fine for a trial on one machine; use PostgreSQL if more than one process writes",
        )
    else:
        add(OK, "DATABASE_URL", "set")

    roles = [i.role for i in cfg.api_identities]
    if not cfg.api_identities:
        add(
            FAIL,
            "API_KEYS",
            "no identities: every endpoint would refuse requests (make keys with scripts.make_api_key)",
        )
    else:
        reporters = [i for i in cfg.api_identities if i.role == "reporter"]
        reviewers = [i for i in cfg.api_identities if i.role in ("reviewer", "admin")]
        if not reporters:
            add(
                WARN,
                "API_KEYS",
                "no reporter identity: alerts would be sent with a reviewer key, which the four-eyes rule then blocks",
            )
        if not reviewers:
            add(FAIL, "API_KEYS", "no reviewer identity: nobody can approve or reject a proposal")
        if len(cfg.api_identities) < 2:
            add(WARN, "API_KEYS", "one identity only: with a single key the same person submits and decides")
        if reporters and reviewers:
            add(OK, "API_KEYS", f"{len(cfg.api_identities)} identities ({', '.join(sorted(set(roles)))})")
    if cfg.allow_self_approval:
        add(
            WARN,
            "ALLOW_SELF_APPROVAL",
            "true: the submitter can approve their own incident (fine solo, not for a pilot of the process)",
        )
    else:
        add(OK, "ALLOW_SELF_APPROVAL", "false (four-eyes rule on)")

    if cfg.allowed_repositories:
        add(OK, "ALLOWED_REPOSITORIES", ", ".join(sorted(cfg.allowed_repositories)))
    else:
        add(FAIL, "ALLOWED_REPOSITORIES", "empty: every repository is refused")

    if cfg.enable_github_remediation:
        add(OK, "ENABLE_GITHUB_REMEDIATION", "true")
    else:
        add(FAIL, "ENABLE_GITHUB_REMEDIATION", "false: analyses can be reviewed but no pull request is ever opened")
    if cfg.require_remediation_approval:
        add(OK, "REQUIRE_REMEDIATION_APPROVAL", "true")
    else:
        add(
            FAIL,
            "REQUIRE_REMEDIATION_APPROVAL",
            "false: a pull request would open without a human decision; a pilot needs it on",
        )
    add(OK, "GITHUB_PR_DRAFT", "true") if cfg.github_pr_draft else add(
        WARN, "GITHUB_PR_DRAFT", "false: pull requests would not be drafts"
    )

    if not cfg.github_token:
        add(FAIL, "GITHUB_TOKEN", "not set: source cannot be read and no pull request can be opened")
    elif not cfg.github_token.startswith(_TOKEN_PREFIXES):
        add(WARN, "GITHUB_TOKEN", "set, but it does not look like a GitHub token (unexpected prefix)")
    else:
        add(OK, "GITHUB_TOKEN", "set")

    if cfg.sandbox_enabled:
        add(OK, "SANDBOX_ENABLED", f"true (image {cfg.sandbox_image})")
    else:
        add(
            NOTE,
            "SANDBOX_ENABLED",
            "false: the repository's tests and the model's reproduction test are not run (shown as proposed only)",
        )
    add(
        NOTE, "HISTORY_REVIEWER_NOTES", "true: earlier reviewer decisions are shown to the model"
    ) if cfg.history_reviewer_notes else add(
        NOTE, "HISTORY_REVIEWER_NOTES", "false: earlier reviewer decisions are not shown to the model"
    )
    if not cfg.notify_webhook_url:
        add(NOTE, "NOTIFY_WEBHOOK_URL", "not set: reviewers are not notified; they must open the console")
    return found


def database() -> list[Finding]:
    """Can we reach the database, and is it migrated to the version this code expects?"""
    try:
        from alembic.config import Config
        from alembic.migration import MigrationContext
        from alembic.script import ScriptDirectory
        from sqlalchemy.exc import SQLAlchemyError

        from src.db.client import get_engine, ping_database

        try:
            ping_database()
        except SQLAlchemyError:
            return [Finding(FAIL, "database", "cannot connect (check DATABASE_URL and that the server is running)")]
        cfg = Config("alembic.ini")
        cfg.set_main_option("script_location", "migrations")
        head = ScriptDirectory.from_config(cfg).get_current_head()
        with get_engine().connect() as connection:
            current = MigrationContext.configure(connection).get_current_revision()
    except Exception as exc:  # noqa: BLE001  (a broken setup is a finding, not a traceback)
        return [Finding(FAIL, "database", f"check failed ({type(exc).__name__})")]
    if current != head:
        return [
            Finding(
                FAIL,
                "database",
                f"schema is at {current or 'nothing'}, code expects {head}: run `alembic upgrade head`",
            )
        ]
    return [Finding(OK, "database", f"reachable, schema at {head}")]


def github(cfg: Any, open_repo: Callable[[str], Any]) -> list[Finding]:
    """Read-only look at each allowed repository: reachable, can the token write, is it archived."""
    found: list[Finding] = []
    for name in sorted(cfg.allowed_repositories):
        try:
            repo = open_repo(name)
            permissions = getattr(repo, "permissions", None)
            visibility = "private" if getattr(repo, "private", False) else "public"
            if getattr(repo, "archived", False):
                found.append(Finding(FAIL, f"github:{name}", "archived: nothing can be pushed"))
            elif permissions is not None and not getattr(permissions, "push", False):
                found.append(
                    Finding(
                        FAIL,
                        f"github:{name}",
                        f"{visibility}; the token cannot push (needs Contents and Pull requests write)",
                    )
                )
            else:
                found.append(
                    Finding(OK, f"github:{name}", f"{visibility}, default branch {repo.default_branch}, token can push")
                )
                if visibility == "public":
                    found.append(
                        Finding(NOTE, f"github:{name}", "public: use a private throwaway repository for a first pilot")
                    )
        except Exception as exc:  # noqa: BLE001  (the category is enough; never echo a response body)
            found.append(
                Finding(FAIL, f"github:{name}", f"cannot open ({getattr(exc, 'category', type(exc).__name__)})")
            )
    return found


def docker(cfg: Any, run: Callable[[list[str]], int] | None = None) -> list[Finding]:
    """With the sandbox on: is a Docker daemon answering, and is the image already here (the sandbox has no network)?"""
    if not cfg.sandbox_enabled:
        return []
    run = run or _run

    if run(["docker", "info"]) != 0:
        return [Finding(FAIL, "docker", "no Docker daemon answers (start Docker, or set SANDBOX_ENABLED=false)")]
    if run(["docker", "image", "inspect", cfg.sandbox_image]) != 0:
        return [
            Finding(
                FAIL,
                "docker",
                f"image {cfg.sandbox_image} is not present locally: the sandbox has no network, build or pull it first",
            )
        ]
    return [Finding(OK, "docker", f"daemon answers, image {cfg.sandbox_image} present")]


def _run(argv: list[str]) -> int:
    try:
        return subprocess.run(argv, capture_output=True, timeout=30, check=False).returncode
    except (OSError, subprocess.TimeoutExpired):
        return 1


def render(findings: list[Finding]) -> str:
    mark = {OK: "ok  ", WARN: "warn", FAIL: "FAIL", NOTE: "note"}
    lines = [f"[{mark[f.level]}] {f.name}: {f.message}" for f in findings]
    blocking = sum(f.level == FAIL for f in findings)
    warnings = sum(f.level == WARN for f in findings)
    lines.append("")
    lines.append(
        f"{blocking} blocking, {warnings} warning(s). "
        + ("Ready for a pilot." if not blocking else "Not ready: fix the FAIL lines first.")
    )
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--online", action="store_true", help="also make read-only GitHub calls and check Docker")
    args = parser.parse_args(argv)
    from src.config import settings

    findings = configuration(settings)
    findings += database()
    if args.online:
        if settings.github_token and settings.allowed_repositories:
            from src.integrations.github import get_repo

            findings += github(settings, get_repo)
        findings += docker(settings)
    print(render(findings))
    return 1 if any(f.level == FAIL for f in findings) else 0


if __name__ == "__main__":
    raise SystemExit(main())
