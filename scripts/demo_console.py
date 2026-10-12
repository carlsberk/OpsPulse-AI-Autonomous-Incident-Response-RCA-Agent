"""One command to look at the review console with demo data (no Docker, no PostgreSQL, no API keys).

    python -m scripts.demo_console                  # http://127.0.0.1:8000/console , key: demo-key
    python -m scripts.demo_console --port 8080
    python -m scripts.demo_console --no-serve       # only prepare the database (used by the tests)

It creates a throwaway SQLite database (in the system temp directory), applies the migrations, runs ten evaluation
cases through the real pipeline with scripted (MOCK) model replies, and starts the API with a fixed local key. The
LLM is switched off (`GROQ_API_KEY` blank), so the guidance and rule answers work and model questions degrade to
the deterministic guidance; nothing leaves your machine. Approving a proposal tries GitHub with a placeholder token
and ends as `pr_failed`, which is the honest outcome without a real token.
"""

from __future__ import annotations

import argparse
import os
import pathlib
import sys
import tempfile

DEMO_KEY = "demo-key"
ROOT = pathlib.Path(__file__).resolve().parents[1]


def prepare(db_path: pathlib.Path) -> None:
    """Set the environment *before* any `src` import (settings are read once at import), migrate and seed."""
    os.environ.update(
        {
            "DATABASE_URL": f"sqlite:///{db_path.as_posix()}",
            "API_KEY": DEMO_KEY,
            "CONSOLE_DEMO_KEY": DEMO_KEY,  # the page connects by itself; never use this on a real server
            "API_KEYS": "",
            "EMBEDDED_WORKER": "false",
            "GROQ_API_KEY": "",  # offline: deterministic guidance only
            "LANGFUSE_PUBLIC_KEY": "",
            "LANGFUSE_SECRET_KEY": "",
            "SENTRY_CLIENT_SECRET": "",
            "ALLOW_UNAUTHENTICATED": "false",
            # Remediation on with a placeholder token, set here because `src.config` is read once at import time
            # (alembic imports it before the seed script's own defaults would apply). Approvals therefore wait
            # for a decision; approving ends as `pr_failed`, the honest outcome without a real token.
            "ENABLE_GITHUB_REMEDIATION": "true",
            "GITHUB_TOKEN": "demo-placeholder-not-a-token",
            "REQUIRE_REMEDIATION_APPROVAL": "true",
        }
    )
    os.chdir(ROOT)
    sys.path.insert(0, str(ROOT))
    from alembic import command
    from alembic.config import Config

    command.upgrade(Config(str(ROOT / "alembic.ini")), "head")
    from scripts import seed_demo_data

    seed_demo_data.main()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--db", type=pathlib.Path, help="SQLite file to use (default: a new temp file)")
    parser.add_argument("--no-serve", action="store_true", help="prepare the database and exit")
    args = parser.parse_args(argv)

    db_path = args.db or pathlib.Path(tempfile.mkdtemp(prefix="opspulse-demo-")) / "demo.db"
    if args.db and db_path.exists():
        db_path.unlink()
    prepare(db_path)
    if args.no_serve:
        return 0

    import uvicorn

    print(f"\nConsole:  http://{args.host}:{args.port}/console   (connects with the local demo key by itself)\n")
    uvicorn.run("src.main:app", host=args.host, port=args.port, log_level="warning")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
