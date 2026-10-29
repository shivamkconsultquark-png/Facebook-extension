"""Synchronous SQLAlchemy engine (same approach as daily_twitter_sync.py — no greenlet needed)."""
from contextlib import contextmanager

from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine

from .config import BACKEND_DIR, settings

_engine: Engine | None = None


def get_engine() -> Engine:
    global _engine
    if _engine is None:
        if not settings.database_url:
            raise RuntimeError("Database not configured: set DB_USER/DB_PASS/DB_HOST/DB_NAME or DATABASE_URL")
        # Session time zone pinned to UTC so CURRENT_TIMESTAMP defaults match UTC_TIMESTAMP() queries.
        _engine = create_engine(settings.database_url, pool_pre_ping=True, pool_recycle=1800,
                                connect_args={"init_command": "SET time_zone = '+00:00'"})
    return _engine


def set_engine(engine: Engine) -> None:
    """Used by tests to point the app at a dedicated test database."""
    global _engine
    _engine = engine


@contextmanager
def connect():
    """Transactional connection: commits on success, rolls back on error."""
    with get_engine().begin() as conn:
        yield conn


def ping() -> bool:
    try:
        with get_engine().connect() as conn:
            conn.execute(text("SELECT 1"))
        return True
    except Exception:
        return False


MIGRATIONS_DIR = BACKEND_DIR / "migrations"


def pending_migrations() -> list[str] | None:
    """Migration files not yet applied (run migrate.py), or None if the database can't be read."""
    try:
        with get_engine().connect() as conn:
            done = {r[0] for r in conn.execute(text("SELECT version FROM schema_migrations"))}
    except Exception:
        return None
    return [p.stem for p in sorted(MIGRATIONS_DIR.glob("*.sql")) if p.stem not in done]
