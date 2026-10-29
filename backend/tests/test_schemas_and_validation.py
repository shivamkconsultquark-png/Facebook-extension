"""Request validation that runs without a database."""
import pytest
from fastapi.testclient import TestClient

from app.auth import current_user, hash_password, verify_password
from app.main import app
from app.platforms.facebook.schemas import FacebookPost
from app.ratelimit import SlidingWindowLimiter


@pytest.fixture()
def api():
    app.dependency_overrides[current_user] = lambda: {"id": 1, "email": "t@example.com", "token": "x"}
    yield TestClient(app)
    app.dependency_overrides.clear()


def test_post_key_prefers_post_id_then_permalink_then_content():
    assert FacebookPost(client_id="a", post_id="123", text="x").post_key() == "fb:123"
    p1 = FacebookPost(client_id="a", permalink="https://www.facebook.com/x/posts/1?__cft__=abc", text="x")
    p2 = FacebookPost(client_id="b", permalink="https://web.facebook.com/x/posts/1", text="y")
    assert p1.post_key() == p2.post_key()
    c1 = FacebookPost(client_id="a", text="Hello  there", author_name="Ann")
    c2 = FacebookPost(client_id="b", text="hello there", author_name="ann")
    assert c1.post_key() == c2.post_key()


def test_non_facebook_permalinks_are_dropped():
    p = FacebookPost(client_id="a", permalink="https://evil.example/posts/1", author_url="javascript:alert(1)")
    assert p.permalink is None and p.author_url is None


def test_tracking_params_stripped_from_permalink():
    p = FacebookPost(client_id="a", permalink="https://m.facebook.com/permalink.php?story_fbid=99&id=7&__tn__=R")
    assert p.permalink == "https://www.facebook.com/permalink.php?id=7&story_fbid=99"


def test_bad_post_id_ignored_and_urls_filtered():
    p = FacebookPost(client_id="a", post_id="<script>", urls=["javascript:x", "https://ok.example", "ftp://x"])
    assert p.post_id is None
    assert p.urls == ["https://ok.example"]


def test_unauthenticated_requests_rejected():
    c = TestClient(app)
    for method, path in [("get", "/facebook/feed"), ("get", "/facebook/browser-scraper/status"),
                         ("post", "/facebook/browser-scraper/start"), ("post", "/facebook/browser-scraper/import-session")]:
        assert getattr(c, method)(path).status_code == 401, path


def test_validation_errors_do_not_echo_submitted_values(api):
    secret = "my-private-cookie-" * 10
    r = api.post("/facebook/browser-scraper/start", json={"interval_seconds": secret})
    assert r.status_code == 422
    assert secret not in r.text


def test_scraper_interval_must_be_one_of_the_dropdown_values(api):
    assert api.post("/facebook/browser-scraper/start", json={"interval_seconds": 5}).status_code == 422


def test_feed_param_validation(api):
    assert api.get("/facebook/feed", params={"sort": "drop table"}).status_code == 422
    assert api.get("/facebook/feed", params={"date_from": "2026-1-1"}).status_code == 422
    assert api.get("/facebook/feed", params={"limit": 1000}).status_code == 422
    assert api.get("/facebook/feed", params={"category": "nope"}).status_code == 422


def test_feed_rejects_reversed_or_impossible_date_ranges(api):
    r = api.get("/facebook/feed", params={"date_from": "2026-10-27", "date_to": "2026-10-01"})
    assert r.status_code == 422 and r.json()["detail"] == "date_from must be on or before date_to"
    r = api.get("/facebook/feed", params={"date_from": "2026-02-31"})
    assert r.status_code == 422 and r.json()["detail"] == "date_from is not a real date"


def test_oversized_body_rejected(api):
    r = api.post("/facebook/browser-scraper/import-session", content=b"{" + b" " * (600 * 1024) + b"}",
                 headers={"content-type": "application/json"})
    assert r.status_code == 413


def test_password_hashing_roundtrip():
    h = hash_password("correct horse battery")
    assert verify_password("correct horse battery", h)
    assert not verify_password("wrong", h)
    assert not verify_password("x", "garbage")


def test_rate_limiter():
    rl = SlidingWindowLimiter(limit=3, window_seconds=60)
    assert rl.try_acquire("u", 2)[0]
    assert rl.try_acquire("u", 1)[0]
    ok, retry = rl.try_acquire("u", 1)
    assert not ok and retry >= 1
    assert rl.try_acquire("other", 3)[0]


def test_facebook_controls_stripped_from_stored_text():
    p = FacebookPost(client_id="a", text="BEWARE OF THIS SCAM D... See more")
    assert p.text == "BEWARE OF THIS SCAM D"
    assert FacebookPost(client_id="a", text="Line one\nLine two See translation").text == "Line one\nLine two"
