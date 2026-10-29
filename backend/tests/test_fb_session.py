"""Cookie-Editor import parsing and session encryption (no database / browser needed)."""
import json
import time

import pytest

from app.platforms.facebook import session as s

COOKIE_EDITOR_EXPORT = json.dumps([
    {"domain": ".facebook.com", "expirationDate": time.time() + 86400 * 300, "hostOnly": False, "httpOnly": False,
     "name": "c_user", "path": "/", "sameSite": "no_restriction", "secure": True, "session": False, "value": "100012345678"},
    {"domain": ".facebook.com", "expirationDate": time.time() + 86400 * 300, "hostOnly": False, "httpOnly": True,
     "name": "xs", "path": "/", "sameSite": "no_restriction", "secure": True, "session": False, "value": "12%3Aabc%3A2"},
    {"domain": ".facebook.com", "httpOnly": True, "name": "datr", "path": "/", "sameSite": "unspecified",
     "secure": True, "session": True, "value": "zzz"},
    {"domain": ".instagram.com", "name": "sessionid", "value": "not-ours", "path": "/"},
])


def test_cookie_editor_json_is_converted_to_playwright_cookies():
    cookies = s.parse_cookies(COOKIE_EDITOR_EXPORT)
    by = {c["name"]: c for c in cookies}
    assert set(by) == {"c_user", "xs", "datr"}  # non-Facebook cookies dropped
    assert by["c_user"]["sameSite"] == "None" and by["c_user"]["secure"] is True
    assert by["datr"]["sameSite"] == "Lax" and "expires" not in by["datr"]
    assert by["xs"]["httpOnly"] is True and by["xs"]["expires"] > time.time()
    assert s.account_id(cookies) == "100012345678"


def test_cookie_string_and_object_formats():
    assert {c["name"] for c in s.parse_cookies("c_user=1; xs=abc; fr=q")} == {"c_user", "xs", "fr"}
    assert {c["name"] for c in s.parse_cookies(json.dumps({"c_user": "1", "xs": "abc"}))} == {"c_user", "xs"}
    wrapped = json.dumps({"cookies": json.loads(COOKIE_EDITOR_EXPORT)})
    assert s.account_id(s.parse_cookies(wrapped)) == "100012345678"


@pytest.mark.parametrize("raw,message", [
    ("", "Paste"),
    ("hello world", "not valid JSON"),
    (json.dumps([{"name": "datr", "value": "x", "domain": ".facebook.com"}]), "c_user, xs"),
    ("c_user=1", "xs"),
    (json.dumps("just a string"), "Expected the Cookie-Editor JSON list"),
])
def test_bad_imports_give_helpful_messages(raw, message):
    with pytest.raises(s.CookieError, match=message):
        s.parse_cookies(raw)


def test_expired_login_cookies_rejected():
    data = json.loads(COOKIE_EDITOR_EXPORT)
    data[0]["expirationDate"] = time.time() - 60
    with pytest.raises(s.CookieError, match="expired"):
        s.parse_cookies(json.dumps(data))


def test_encryption_roundtrip_and_tamper_detection(monkeypatch):
    from cryptography.fernet import Fernet
    monkeypatch.setattr(s.settings, "session_encryption_key", Fernet.generate_key().decode())
    cookies = s.parse_cookies("c_user=1; xs=secret-value")
    blob = s.encrypt(cookies)
    assert "secret-value" not in blob
    assert s.decrypt(blob) == cookies
    assert s.decrypt(blob[:-4] + "AAAA") is None
    monkeypatch.setattr(s.settings, "session_encryption_key", Fernet.generate_key().decode())
    assert s.decrypt(blob) is None  # wrong key


def test_profile_url_only_for_numeric_ids():
    from app.platforms.facebook.scraper import profile_url
    assert profile_url("100012345678") == "https://www.facebook.com/profile.php?id=100012345678"
    assert profile_url("javascript:alert(1)") is None
    assert profile_url(None) is None
