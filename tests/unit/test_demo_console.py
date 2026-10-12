"""`python -m scripts.demo_console` prepares a working demo database with one command."""

import os
import sqlite3
import subprocess
import sys


def test_demo_console_prepares_a_migrated_and_seeded_database(tmp_path):
    db = tmp_path / "demo.db"
    env = {k: v for k, v in os.environ.items() if k not in ("DATABASE_URL", "API_KEY", "API_KEYS")}
    proc = subprocess.run(  # noqa: S603 - fixed arguments, no untrusted input
        [sys.executable, "-m", "scripts.demo_console", "--no-serve", "--db", str(db)],
        capture_output=True,
        text=True,
        env=env,
        timeout=180,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr[-800:]
    with sqlite3.connect(db) as conn:
        total = conn.execute("select count(*) from incidents").fetchone()[0]
        statuses = {row[0] for row in conn.execute("select distinct status from incidents")}
    assert total == 10 and {"needs_review", "awaiting_approval"} <= statuses
