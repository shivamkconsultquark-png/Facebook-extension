"""Can Facebook's embedded player play a video? (otherwise it shows "Video unavailable")."""
from contextlib import contextmanager

import httpx
import pytest
from fastapi.testclient import TestClient

from app import db, repository as repo
from app.auth import current_user
from app.main import app
from app.platforms.facebook import embed


class Resp:
    def __init__(self, status, body):
        self.status_code, self.text = status, body


@pytest.fixture(autouse=True)
def fresh_cache():
    embed._cache.clear()
    yield
    embed._cache.clear()


def test_player_page_with_video_element_is_playable(monkeypatch):
    calls = []
    monkeypatch.setattr(embed.httpx, "get", lambda *a, **k: calls.append(k["params"]["href"]) or Resp(200, "<div><video src=x></video></div>"))
    assert embed.playable("https://www.facebook.com/reel/1") is True
    assert embed.playable("https://www.facebook.com/reel/1") is True
    assert calls == ["https://www.facebook.com/reel/1"]  # cached


def test_player_page_without_video_is_not_playable(monkeypatch):
    monkeypatch.setattr(embed.httpx, "get", lambda *a, **k: Resp(200, "<div>Video unavailable</div>"))
    assert embed.playable("https://www.facebook.com/reel/2") is False


def test_unknown_when_facebook_cannot_be_asked(monkeypatch):
    def boom(*a, **k):
        raise httpx.ConnectError("offline")
    monkeypatch.setattr(embed.httpx, "get", boom)
    assert embed.playable("https://www.facebook.com/reel/3") is None
    monkeypatch.setattr(embed.httpx, "get", lambda *a, **k: Resp(500, ""))
    assert embed.playable("https://www.facebook.com/reel/3") is None


@pytest.fixture()
def api(monkeypatch):
    @contextmanager
    def no_db():
        yield None
    monkeypatch.setattr(db, "connect", no_db)
    app.dependency_overrides[current_user] = lambda: {"id": 1, "email": "t@example.com", "token": "x"}
    yield TestClient(app)
    app.dependency_overrides.clear()


def test_endpoint_checks_only_the_users_stored_video(api, monkeypatch):
    seen = []
    monkeypatch.setattr(repo, "video_url", lambda conn, uid, pid: seen.append((uid, pid)) or "https://www.facebook.com/reel/9")
    monkeypatch.setattr(embed, "playable", lambda url: url.endswith("/9") and False)
    r = api.get("/facebook/feed/42/playable")
    assert r.status_code == 200 and r.json() == {"playable": False}
    assert seen == [(1, 42)]


def test_endpoint_404_without_video(api, monkeypatch):
    monkeypatch.setattr(repo, "video_url", lambda conn, uid, pid: None)
    assert api.get("/facebook/feed/42/playable").status_code == 404
