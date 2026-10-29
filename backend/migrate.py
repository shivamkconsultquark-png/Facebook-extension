#!/usr/bin/env python3
"""Apply versioned SQL migrations in backend/migrations (NNN_name.sql) exactly once.

Usage:  python migrate.py            # apply pending
        python migrate.py --status   # list applied / pending
"""
import pathlib
import sys

from sqlalchemy import text

sys.path.insert(0, str(pathlib.Path(__file__).parent))
from app.db import get_engine  # noqa: E402

MIGRATIONS_DIR = pathlib.Path(__file__).parent / "migrations"


def _statements(sql: str):
    lines = [ln for ln in sql.splitlines() if not ln.strip().startswith("--")]
    for stmt in "\n".join(lines).split(";"):
        if stmt.strip():
            yield stmt


def migrate(engine=None, verbose=True) -> list[str]:
    engine = engine or get_engine()
    applied_now = []
    with engine.begin() as conn:
        conn.execute(text(
            "CREATE TABLE IF NOT EXISTS schema_migrations ("
            " version VARCHAR(100) NOT NULL PRIMARY KEY,"
            " applied_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP)"
        ))
        done = {r[0] for r in conn.execute(text("SELECT version FROM schema_migrations"))}
    for path in sorted(MIGRATIONS_DIR.glob("*.sql")):
        if path.stem in done:
            continue
        # MySQL DDL auto-commits, so each statement runs on its own; IF NOT EXISTS keeps reruns safe.
        with engine.begin() as conn:
            for stmt in _statements(path.read_text(encoding="utf-8")):
                conn.execute(text(stmt))
            conn.execute(text("INSERT INTO schema_migrations (version) VALUES (:v)"), {"v": path.stem})
        applied_now.append(path.stem)
        if verbose:
            print(f"applied {path.stem}")
    if verbose and not applied_now:
        print("database is up to date")
    return applied_now


def status():
    with get_engine().begin() as conn:
        try:
            done = {r[0] for r in conn.execute(text("SELECT version FROM schema_migrations"))}
        except Exception:
            done = set()
    for path in sorted(MIGRATIONS_DIR.glob("*.sql")):
        print(("[x] " if path.stem in done else "[ ] ") + path.stem)


if __name__ == "__main__":
    status() if "--status" in sys.argv else migrate()
