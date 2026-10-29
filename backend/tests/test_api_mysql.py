"""End-to-end API tests against a real MySQL database (requires TEST_DATABASE_URL).

The browser layer is replaced with fakes so the scraper's orchestration (import → start →
cycle → feed, session expiry, stop, switch account) is tested deterministically.
"""
import time
import uuid

import pytest

SCAM = ("Congratulations! You have been selected as our lucky winner. Claim your prize now — "
        "pay a small delivery fee with a gift card.")
LEGIT = "Had a great weekend hiking with the family at the lake! Weather was perfect."
COOKIES = "c_user=100012345678; xs=abc%3Adef; datr=zz"


def scraped(pid, text, author="Prize Desk", **extra):
    return {"post": {"post_id": pid, "permalink": f"https://www.facebook.com/x/posts/{pid}", "text": text,
                     "urls": [], "author_name": author, "author_url": "https://www.facebook.com/x"}, **extra}


class FakeBrowser:
    def __init__(self):
        self.logged_in = True
        self.posts = [scraped("1001", SCAM, media_url="https://scontent.xx.fbcdn.net/a.jpg", comments_count="12"),
                      scraped("1002", LEGIT, author="Ann"),
                      scraped("1003", "ok")]
        self.browse_calls = 0

    def browse(self, cookies, want_name=False, skip_albums=None):
        self.browse_calls += 1
        if not self.logged_in:
            return {"logged_in": False, "reason": "The Facebook session is logged out or expired.", "posts": []}
        return {"logged_in": True, "posts": self.posts, "cookies": cookies, "account_name": "Test User" if want_name else None}

    def verify(self, cookies):
        return {"ok": self.logged_in, "reason": "The Facebook session is logged out or expired.",
                "cookies": cookies, "account_name": "Test User"}


@pytest.fixture()
def fake(client, monkeypatch):
    from app.platforms.facebook import scraper
    fb = FakeBrowser()
    monkeypatch.setattr(scraper.manager, "browse", fb.browse)
    monkeypatch.setattr(scraper.manager, "verify", fb.verify)
    monkeypatch.setattr(scraper.settings, "scraper_allow_login_window", False)
    yield fb
    scraper.manager.shutdown()


def _register(client):
    email = f"u{uuid.uuid4().hex[:10]}@example.com"
    r = client.post("/auth/register", json={"email": email, "password": "s3cret-pass!"})
    assert r.status_code == 201, r.text
    return {"Authorization": f"Bearer {r.json()['token']}"}, email


def _wait(client, headers, pred, timeout=10):
    deadline = time.time() + timeout
    while time.time() < deadline:
        st = client.get("/facebook/browser-scraper/status", headers=headers).json()
        if pred(st):
            return st
        time.sleep(0.2)
    raise AssertionError(f"condition not met; last status: {st}")


def test_health(client):
    r = client.get("/health")
    assert r.status_code == 200 and r.json()["database"] is True


def test_register_login_logout(client):
    headers, email = _register(client)
    assert client.post("/auth/register", json={"email": email, "password": "s3cret-pass!"}).status_code == 409
    assert client.post("/auth/login", json={"email": email, "password": "wrong-pass!"}).status_code == 401
    r = client.post("/auth/login", json={"email": email, "password": "s3cret-pass!"})
    h2 = {"Authorization": f"Bearer {r.json()['token']}"}
    assert client.get("/auth/me", headers=h2).json()["email"] == email
    assert client.post("/auth/logout", headers=h2).status_code == 200
    assert client.get("/auth/me", headers=h2).status_code == 401
    assert client.get("/auth/me", headers=headers).status_code == 200


def test_full_scraper_flow(client, fake):
    headers, _ = _register(client)
    st = client.get("/facebook/browser-scraper/status", headers=headers).json()
    assert st["running"] is False and st["fb_account"] is None and st["interval_seconds"] == 300

    # Start is refused until an account is linked (login window disabled here).
    assert client.post("/facebook/browser-scraper/start", json={"interval_seconds": 300}, headers=headers).status_code == 400

    bad = client.post("/facebook/browser-scraper/import-session", json={"cookies": "datr=1"}, headers=headers).json()
    assert bad["ok"] is False and "c_user" in bad["detail"]
    ok = client.post("/facebook/browser-scraper/import-session", json={"cookies": COOKIES}, headers=headers).json()
    assert ok["ok"] is True and "Test User" in ok["detail"]
    st = client.get("/facebook/browser-scraper/status", headers=headers).json()
    assert st["fb_account"] == "Test User"
    assert st["fb_account_url"] == "https://www.facebook.com/profile.php?id=100012345678"

    client.post("/facebook/browser-scraper/start", json={"interval_seconds": 600}, headers=headers)
    st = _wait(client, headers, lambda s: s["cycles"] >= 1)
    assert st["running"] and st["interval_seconds"] == 600 and st["next_run_at"]
    assert st["last_count"] == 2 and st["total_scraped"] == 2  # "ok" is too short to score
    assert st["needs_login"] is False and st["last_run"]

    feed = client.get("/facebook/feed", headers=headers).json()
    assert feed["total"] == 2
    scam = next(p for p in feed["feed"] if p["post_key"] == "fb:1001")
    assert scam["category"] == "likely_scam" and scam["spam_score"] >= 70
    assert scam["media_url"] == "https://scontent.xx.fbcdn.net/a.jpg" and scam["comments_count"] == "12"
    assert scam["user"]["name"] == "Prize Desk"
    assert client.get("/facebook/feed", params={"category": "spam"}, headers=headers).json()["total"] == 1
    assert client.get("/facebook/feed", params={"hide_above": 50}, headers=headers).json()["total"] == 1
    assert client.get("/facebook/feed", params={"search": "hiking"}, headers=headers).json()["total"] == 1

    cfg = client.get("/facebook/config", headers=headers).json()["config"]
    assert cfg["total_posts"] == 2 and cfg["spam_spam"] == 1 and cfg["spam_clean"] == 1

    # Session expires → keeps running, reports login required; a fresh import resumes it.
    fake.logged_in = False
    from app.platforms.facebook.scraper import manager
    user_id = client.get("/auth/me", headers=headers).json()["id"]
    manager._wake(user_id)
    st = _wait(client, headers, lambda s: s["needs_login"])
    assert st["running"] is True and "Login required" in st["message"]
    fake.logged_in = True
    assert client.post("/facebook/browser-scraper/import-session", json={"cookies": COOKIES}, headers=headers).json()["ok"]
    st = _wait(client, headers, lambda s: not s["needs_login"] and s["cycles"] >= 2)
    assert st["total_scraped"] == 2  # same posts again: refreshed, not new

    client.post("/facebook/browser-scraper/stop", headers=headers)
    st = _wait(client, headers, lambda s: not s["running"])
    assert st["fb_account"] == "Test User" and st["interval_seconds"] == 600

    client.post("/facebook/browser-scraper/switch-account", headers=headers)
    st = client.get("/facebook/browser-scraper/status", headers=headers).json()
    assert st["fb_account"] is None and st["running"] is False


def test_sessions_are_encrypted_at_rest(client, fake, mysql_engine):
    from sqlalchemy import text
    headers, _ = _register(client)
    client.post("/facebook/browser-scraper/import-session", json={"cookies": COOKIES}, headers=headers)
    user_id = client.get("/auth/me", headers=headers).json()["id"]
    with mysql_engine.begin() as conn:
        blob = conn.execute(text("SELECT cookies_enc FROM fb_sessions WHERE user_id = :u"), {"u": user_id}).scalar()
    assert blob and "abc%3Adef" not in blob and "100012345678" not in blob


def test_scraper_state_resumes_after_restart(client, fake, monkeypatch):
    from app.platforms.facebook.scraper import manager, settings
    monkeypatch.setattr(settings, "scraper_resume", True)
    headers, _ = _register(client)
    client.post("/facebook/browser-scraper/import-session", json={"cookies": COOKIES}, headers=headers)
    client.post("/facebook/browser-scraper/start", json={"interval_seconds": 900}, headers=headers)
    _wait(client, headers, lambda s: s["cycles"] >= 1)
    manager.shutdown()
    manager._workers.clear()
    _wait(client, headers, lambda s: not s["running"])
    manager.resume_all()
    st = _wait(client, headers, lambda s: s["running"])
    assert st["interval_seconds"] == 900


def test_users_cannot_see_each_others_data(client, fake):
    h1, _ = _register(client)
    h2, _ = _register(client)
    client.post("/facebook/browser-scraper/import-session", json={"cookies": COOKIES}, headers=h1)
    client.post("/facebook/browser-scraper/start", json={"interval_seconds": 300}, headers=h1)
    _wait(client, h1, lambda s: s["cycles"] >= 1)
    assert client.get("/facebook/feed", headers=h2).json()["total"] == 0
    assert client.get("/facebook/browser-scraper/status", headers=h2).json()["fb_account"] is None


def test_settings_roundtrip(client):
    headers, _ = _register(client)
    assert client.get("/facebook/settings/llm-instructions", headers=headers).json()["is_default"]
    r = client.post("/facebook/settings/llm-instructions", json={"instructions": "Custom rules"}, headers=headers)
    assert r.json()["instructions"] == "Custom rules"
    assert client.post("/facebook/settings/llm-instructions", json={"instructions": "  "}, headers=headers).status_code == 422
    assert client.post("/facebook/settings/llm-instructions", json={"restore_default": True}, headers=headers).json()["is_default"]


def test_retention_purge(client, fake, mysql_engine):
    from sqlalchemy import text
    from app import repository
    headers, _ = _register(client)
    client.post("/facebook/browser-scraper/import-session", json={"cookies": COOKIES}, headers=headers)
    client.post("/facebook/browser-scraper/start", json={"interval_seconds": 300}, headers=headers)
    _wait(client, headers, lambda s: s["cycles"] >= 1)
    client.post("/facebook/browser-scraper/stop", headers=headers)
    user_id = client.get("/auth/me", headers=headers).json()["id"]
    with mysql_engine.begin() as conn:
        conn.execute(text("UPDATE fb_post_analyses SET analyzed_at = UTC_TIMESTAMP() - INTERVAL 400 DAY WHERE user_id = :u"), {"u": user_id})
        removed = repository.purge_expired(conn)
    assert removed["analyses"] >= 2
    assert client.get("/facebook/feed", headers=headers).json()["total"] == 0
