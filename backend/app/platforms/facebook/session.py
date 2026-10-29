"""Facebook session import + storage (counterpart of /twitter/browser-scraper/import-session).

Accepts what the AutoScraper page accepts: a Cookie-Editor "Export as JSON" list, a plain
{name: value} object, or a raw "name=value; name2=value2" cookie string. Facebook's login
cookies are `c_user` (account id) and `xs` (session secret) — the analogue of X's `auth_token`.

Sessions are encrypted at rest with Fernet (SESSION_ENCRYPTION_KEY). Without a configured
key one is generated once into backend/.session_key (git-ignored).
"""
from __future__ import annotations

import json
import logging
import pathlib
import time

from cryptography.fernet import Fernet, InvalidToken
from sqlalchemy import text

from ...config import settings

log = logging.getLogger("fbshield.session")

REQUIRED_COOKIES = ("c_user", "xs")
_KEY_FILE = pathlib.Path(__file__).resolve().parents[3] / ".session_key"
_SAMESITE = {"no_restriction": "None", "none": "None", "lax": "Lax", "strict": "Strict"}


class CookieError(ValueError):
    """Raised with a user-facing message when an import can't be used."""


# ---------- parsing
def parse_cookies(raw: str) -> list[dict]:
    """Return Playwright-style cookies for .facebook.com from any supported export format."""
    raw = (raw or "").strip()
    if not raw:
        raise CookieError("Paste your Cookie-Editor export first.")
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        data = None
    if data is None:
        if "=" not in raw:
            raise CookieError("This is not valid JSON. Use Cookie-Editor → Export as JSON, then paste.")
        data = [{"name": k.strip(), "value": v.strip()} for k, _, v in
                (part.partition("=") for part in raw.split(";")) if k.strip()]
    elif isinstance(data, dict):
        if isinstance(data.get("cookies"), list):  # some exporters wrap the list
            data = data["cookies"]
        else:
            data = [{"name": k, "value": str(v)} for k, v in data.items()]
    if not isinstance(data, list):
        raise CookieError('Expected the Cookie-Editor JSON list. Re-export with "Export as JSON".')

    cookies = []
    for c in data:
        if not isinstance(c, dict) or not c.get("name") or c.get("value") in (None, ""):
            continue
        domain = (c.get("domain") or ".facebook.com").strip()
        if "facebook.com" not in domain:
            continue  # only Facebook cookies are kept
        cookie = {
            "name": str(c["name"])[:256],
            "value": str(c["value"])[:4096],
            "domain": domain if domain.startswith(".") or domain == "facebook.com" else domain,
            "path": c.get("path") or "/",
            "secure": bool(c.get("secure", True)),
            "httpOnly": bool(c.get("httpOnly", False)),
            "sameSite": _SAMESITE.get(str(c.get("sameSite", "")).lower(), "Lax"),
        }
        exp = c.get("expirationDate", c.get("expires"))
        if isinstance(exp, (int, float)) and exp > 0:
            cookie["expires"] = float(exp)
        if cookie["sameSite"] == "None":
            cookie["secure"] = True
        cookies.append(cookie)

    names = {c["name"] for c in cookies}
    missing = [n for n in REQUIRED_COOKIES if n not in names]
    if missing:
        raise CookieError(
            f'Found {len(cookies)} Facebook cookies, but not the login cookie(s) {", ".join(missing)}. '
            "Make sure you're logged into facebook.com, then re-export.")
    expired = [c["name"] for c in cookies if c["name"] in REQUIRED_COOKIES
               and c.get("expires") and c["expires"] < time.time()]
    if expired:
        raise CookieError("Your Facebook login cookies have expired. Log into facebook.com again and re-export.")
    return cookies


def account_id(cookies: list[dict]) -> str | None:
    return next((c["value"] for c in cookies if c["name"] == "c_user"), None)


# ---------- encryption
def _fernet() -> Fernet:
    key = settings.session_encryption_key
    if not key:
        if _KEY_FILE.exists():
            key = _KEY_FILE.read_text().strip()
        else:
            key = Fernet.generate_key().decode()
            _KEY_FILE.write_text(key)
            log.warning("SESSION_ENCRYPTION_KEY not set; generated %s (keep it with your backups)", _KEY_FILE)
    return Fernet(key.encode() if isinstance(key, str) else key)


def encrypt(cookies: list[dict]) -> str:
    return _fernet().encrypt(json.dumps(cookies).encode()).decode()


def decrypt(blob: str) -> list[dict] | None:
    try:
        return json.loads(_fernet().decrypt(blob.encode()))
    except (InvalidToken, ValueError):
        log.warning("Stored Facebook session could not be decrypted (key changed?)")
        return None


# ---------- storage
def save(conn, user_id: int, cookies: list[dict], name: str | None = None) -> None:
    conn.execute(text("""
        INSERT INTO fb_sessions (user_id, cookies_enc, account_id, account_name)
        VALUES (:u, :c, :a, :n)
        ON DUPLICATE KEY UPDATE cookies_enc = VALUES(cookies_enc), account_id = VALUES(account_id),
            account_name = COALESCE(VALUES(account_name), IF(account_id = VALUES(account_id), account_name, NULL))
    """), {"u": user_id, "c": encrypt(cookies), "a": account_id(cookies), "n": name})


def set_account_name(conn, user_id: int, name: str | None) -> None:
    if name:
        conn.execute(text("UPDATE fb_sessions SET account_name = :n WHERE user_id = :u"), {"u": user_id, "n": name[:255]})


def load(conn, user_id: int) -> tuple[list[dict] | None, dict | None]:
    row = conn.execute(text("SELECT cookies_enc, account_id, account_name FROM fb_sessions WHERE user_id = :u"),
                       {"u": user_id}).fetchone()
    if not row:
        return None, None
    return decrypt(row[0]), {"id": row[1], "name": row[2]}


def account(conn, user_id: int) -> dict | None:
    row = conn.execute(text("SELECT account_id, account_name FROM fb_sessions WHERE user_id = :u"),
                       {"u": user_id}).fetchone()
    return {"id": row[0], "name": row[1]} if row else None


def delete(conn, user_id: int) -> None:
    conn.execute(text("DELETE FROM fb_sessions WHERE user_id = :u"), {"u": user_id})
