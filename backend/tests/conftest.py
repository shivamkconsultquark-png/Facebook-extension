import os
import pathlib
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

# Unit tests must never call a real LLM or threat-intel API.
os.environ["GEMINI_API_KEY"] = ""
os.environ["SAFE_BROWSING_API_KEY"] = ""
# Tests log to the console only, never into the service's backend/logs.
os.environ["LOG_DIR"] = ""
# A test app must never pick up the real users' running scrapers.
os.environ["SCRAPER_RESUME"] = "false"


@pytest.fixture(scope="session")
def mysql_engine():
    """A throwaway MySQL database (TEST_DATABASE_URL). Integration tests skip without it."""
    url = os.getenv("TEST_DATABASE_URL")
    if not url:
        pytest.skip("TEST_DATABASE_URL not set — skipping MySQL integration tests")
    from sqlalchemy import create_engine, text

    engine = create_engine(url, connect_args={"init_command": "SET time_zone = '+00:00'"})
    with engine.begin() as conn:
        conn.execute(text("SET FOREIGN_KEY_CHECKS = 0"))
        for (table,) in conn.execute(text("SHOW TABLES")).fetchall():
            conn.execute(text(f"DROP TABLE `{table}`"))
        conn.execute(text("SET FOREIGN_KEY_CHECKS = 1"))
    from migrate import migrate

    migrate(engine, verbose=False)
    from app import db

    db.set_engine(engine)
    yield engine
    engine.dispose()


@pytest.fixture()
def client(mysql_engine):
    from fastapi.testclient import TestClient
    from app.main import app

    with TestClient(app) as c:
        yield c
