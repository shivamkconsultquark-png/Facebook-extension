"""Failure handling: database outages, restarts, health checks, configuration loading."""
import os
import pathlib
import subprocess
import sys
import threading
import time
import uuid
from contextlib import contextmanager

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.exc import OperationalError

from app import db
from app.config import Settings, config_problems
from app.platforms.facebook import scraper

BACKEND = pathlib.Path(__file__).resolve().parents[1]


def _db_down():
    raise OperationalError("SELECT 1", {}, Exception("(2003) Can't connect to MySQL server"))


@contextmanager
def _broken_connect():
    _db_down()
    yield


def _wait(pred, timeout=5.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if pred():
            return True
        time.sleep(0.05)
    return False


# ---------- scraper worker survives database outages
def test_worker_keeps_running_when_the_database_fails(monkeypatch):
    calls = {"n": 0}
    real_cycle = scraper.Worker.cycle

    def flaky_cycle(self):
        calls["n"] += 1
        if calls["n"] == 1:
            with _broken_connect():
                pass
        # later cycles: nothing to do (no DB needed)

    monkeypatch.setattr(scraper.Worker, "cycle", flaky_cycle)
    mgr = scraper.ScraperManager(browse=None, verify=None, login=None)
    w = scraper.Worker(mgr, 1, interval=60)
    w.start()
    try:
        assert _wait(lambda: calls["n"] >= 1 and w.snapshot()["error"])
        assert w.is_alive(), "a database error must not end the scraper thread"
        assert "will retry" in w.snapshot()["message"]
        w.wake_event.set()  # next cycle runs instead of waiting the interval
        assert _wait(lambda: calls["n"] >= 2)
        assert w.is_alive()
    finally:
        w.stop_event.set()
        w.wake_event.set()
        w.join(5)
    assert real_cycle is not flaky_cycle


def test_resume_retries_until_the_database_is_reachable(monkeypatch):
    attempts = {"n": 0}

    def resume_all():
        attempts["n"] += 1
        if attempts["n"] < 3:
            _db_down()

    mgr = scraper.ScraperManager(browse=None, verify=None, login=None)
    monkeypatch.setattr(mgr, "resume_all", resume_all)
    stop = threading.Event()
    sleeps = []
    monkeypatch.setattr(stop, "wait", lambda d: sleeps.append(d))  # no real waiting
    t = mgr.resume_in_background(stop)
    t.join(5)
    assert attempts["n"] == 3
    assert sleeps == [2.0, 4.0]  # bounded exponential backoff


def test_resume_backoff_is_capped_and_stops_on_shutdown(monkeypatch):
    mgr = scraper.ScraperManager(browse=None, verify=None, login=None)
    monkeypatch.setattr(mgr, "resume_all", _db_down)
    stop = threading.Event()
    sleeps = []

    def wait(d):
        sleeps.append(d)
        if len(sleeps) >= 8:
            stop.set()

    monkeypatch.setattr(stop, "wait", wait)
    mgr.resume_in_background(stop, max_delay=60).join(5)
    assert max(sleeps) == 60 and len(sleeps) == 8


def test_health_lists_dead_scraper_threads():
    mgr = scraper.ScraperManager(browse=None, verify=None, login=None)
    w = scraper.Worker(mgr, 7, interval=60)  # never started: counts as dead while not stopped
    mgr._workers[7] = w
    assert mgr.health() == {"running": 0, "dead_workers": [7]}
    w.stop_event.set()
    assert mgr.health() == {"running": 0, "dead_workers": []}


# ---------- health endpoints
def test_liveness_does_not_need_the_database(monkeypatch):
    from app.main import app
    monkeypatch.setattr(db, "ping", lambda: False)
    r = TestClient(app).get("/health/live")
    assert r.status_code == 200 and r.json()["status"] == "ok" and r.json()["version"]


def test_readiness_fails_while_the_database_is_down(monkeypatch):
    from app.main import app
    monkeypatch.setattr(db, "ping", lambda: False)
    r = TestClient(app).get("/health")
    assert r.status_code == 503
    assert r.json()["database"] is False and r.json()["status"] == "degraded"


def test_readiness_fails_with_pending_migrations(monkeypatch):
    from app.main import app
    monkeypatch.setattr(db, "ping", lambda: True)
    monkeypatch.setattr(db, "pending_migrations", lambda: ["009_new"])
    r = TestClient(app).get("/health")
    assert r.status_code == 503 and r.json()["migrations_pending"] == ["009_new"]


def test_readiness_ok_when_everything_is_in_place(monkeypatch):
    from app.main import app
    monkeypatch.setattr(db, "ping", lambda: True)
    monkeypatch.setattr(db, "pending_migrations", lambda: [])
    r = TestClient(app).get("/health")
    assert r.status_code == 200, r.json()
    body = r.json()
    assert body["status"] == "ok" and body["scrapers"]["dead_workers"] == [] and body["disk_free_mb"] > 0


def test_pending_migrations_is_none_when_the_database_is_unreachable(monkeypatch):
    class Broken:
        def connect(self):
            _db_down()
    monkeypatch.setattr(db, "get_engine", lambda: Broken())
    assert db.pending_migrations() is None


# ---------- configuration
def test_config_problems_flag_invalid_settings():
    s = Settings()
    s.database_url, s.retention_days, s.scraper_max_browsers, s.log_level = "", 0, 50, "LOUD"
    problems = " ".join(config_problems(s))
    for word in ("database", "RETENTION_DAYS", "SCRAPER_MAX_BROWSERS", "LOG_LEVEL"):
        assert word in problems


def test_env_file_is_found_from_any_working_directory(tmp_path):
    """Services start in another folder; backend/.env must still be read."""
    env_file = BACKEND / ".env"
    if not env_file.exists():
        pytest.skip("no backend/.env on this machine")
    code = ("import sys; sys.path.insert(0, sys.argv[1]); from app.config import settings; "
            "print('configured' if settings.database_url else 'missing')")
    env = {k: v for k, v in os.environ.items() if not k.startswith("DB_") and k != "DATABASE_URL"}
    out = subprocess.run([sys.executable, "-c", code, str(BACKEND)], cwd=tmp_path, env=env,
                         capture_output=True, text=True, timeout=60)
    assert out.stdout.strip() == "configured", out.stderr


# ---------- MySQL: audit trail and retention
def test_failed_logins_are_recorded(client):
    email = f"u{uuid.uuid4().hex[:10]}@example.com"
    assert client.post("/auth/register", json={"email": email, "password": "s3cret-pass!"}).status_code == 201
    assert client.post("/auth/login", json={"email": email, "password": "wrong-password"}).status_code == 401
    assert client.post("/auth/login", json={"email": email, "password": "s3cret-pass!"}).status_code == 200
    with db.connect() as conn:
        events = [r[0] for r in conn.execute(text(
            "SELECT e.event FROM audit_events e JOIN users u ON u.id = e.user_id WHERE u.email = :e ORDER BY e.id"),
            {"e": email})]
    assert events == ["register", "login_failed", "login"]


def test_retention_purges_old_audit_events(client):
    from app import repository
    with db.connect() as conn:
        conn.execute(text("INSERT INTO audit_events (user_id, event, created_at) VALUES "
                          "(NULL, 'old_event', UTC_TIMESTAMP() - INTERVAL 400 DAY), (NULL, 'new_event', UTC_TIMESTAMP())"))
    removed = repository.purge_expired_separately(db.connect)
    with db.connect() as conn:
        left = {r[0] for r in conn.execute(text("SELECT event FROM audit_events WHERE event IN ('old_event', 'new_event')"))}
    # The app's own startup purge may get there first, so check the outcome, not who deleted it.
    assert "audit" in removed and left == {"new_event"}


def test_purge_retries_a_deadlocked_delete_once():
    from app import repository

    class Orig(Exception):
        args = (1213, "Deadlock found")

    class Conn:
        def __init__(self, fail):
            self.fail = fail

        def execute(self, *a):
            if self.fail:
                raise OperationalError("DELETE", {}, Orig())
            return type("R", (), {"rowcount": 1})()

    attempts = []

    @contextmanager
    def connect():
        attempts.append(1)
        yield Conn(fail=len(attempts) == 1)  # only the very first statement deadlocks

    out = repository.purge_expired_separately(connect)
    assert set(out) == {"analyses", "cache", "feedback", "tokens", "audit"}
    assert len(attempts) == 6  # 5 purges + 1 retry


def test_health_is_ready_against_a_migrated_database(client):
    r = client.get("/health")
    assert r.status_code == 200, r.json()
    assert r.json()["migrations_pending"] == [] and r.json()["database"] is True


# ---------- MySQL: duplicate and concurrent requests
def test_concurrent_duplicate_registrations_create_one_account(client):
    from concurrent.futures import ThreadPoolExecutor
    email = f"dup{uuid.uuid4().hex[:8]}@example.com"
    with ThreadPoolExecutor(8) as pool:
        codes = sorted(pool.map(lambda _: client.post("/auth/register", json={"email": email, "password": "s3cret-pass!"}).status_code,
                                range(8)))
    assert codes.count(201) == 1 and codes.count(409) == 7, codes
    with db.connect() as conn:
        assert conn.execute(text("SELECT COUNT(*) FROM users WHERE email = :e"), {"e": email}).scalar() == 1


def test_storing_the_same_posts_concurrently_never_duplicates(client):
    from concurrent.futures import ThreadPoolExecutor
    from app import scoring
    r = client.post("/auth/register", json={"email": f"p{uuid.uuid4().hex[:8]}@example.com", "password": "s3cret-pass!"})
    uid = r.json()["user"]["id"]
    posts = [{"post": {"post_id": f"77{i}", "text": f"Post number {i} about the weekend market", "urls": [],
                       "author_name": "Market", "author_url": "https://www.facebook.com/market"}} for i in range(5)]
    with ThreadPoolExecutor(4) as pool:
        results = list(pool.map(lambda _: scoring.store_posts(uid, posts), range(4)))
    assert all(r["stored"] == 5 for r in results)
    with db.connect() as conn:
        n = conn.execute(text("SELECT COUNT(*) FROM fb_post_analyses WHERE user_id = :u"), {"u": uid}).scalar()
    assert n == 5
    assert sum(r["new"] for r in results) >= 5  # counted as new at least once each


def test_repeated_start_requests_run_one_scraper(client, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    from app.platforms.facebook.scraper import manager
    monkeypatch.setattr(manager, "browse", lambda cookies, want_name=False, skip_albums=None:
                        {"logged_in": True, "posts": [], "cookies": cookies, "account_name": None})
    monkeypatch.setattr(manager, "verify", lambda c: {"ok": True, "cookies": c, "account_name": "T"})
    r = client.post("/auth/register", json={"email": f"s{uuid.uuid4().hex[:8]}@example.com", "password": "s3cret-pass!"})
    h = {"Authorization": f"Bearer {r.json()['token']}"}
    uid = r.json()["user"]["id"]
    client.post("/facebook/browser-scraper/import-session", json={"cookies": "c_user=1000123; xs=a%3Ab"}, headers=h)
    try:
        with ThreadPoolExecutor(6) as pool:
            codes = list(pool.map(lambda _: client.post("/facebook/browser-scraper/start",
                                                        json={"interval_seconds": 900}, headers=h).status_code, range(6)))
        assert all(c == 200 for c in codes), codes
        workers = [t for t in threading.enumerate() if t.name == f"fb-scraper-{uid}" and t.is_alive()]
        assert len(workers) == 1
    finally:
        manager.stop(uid)
