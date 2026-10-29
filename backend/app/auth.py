"""Standalone account + bearer-token auth.

The Twitter page authenticates against the main finance.opcozero.com app (useAuth() +
Bearer token + X-Company-Id / X-Table-Prefix headers), whose backend was not supplied.
To plug this service into that app, replace `current_user` with a dependency that
validates the main app's token and returns {"id": ..., "email": ...}.
"""
from __future__ import annotations

import hashlib
import hmac
import secrets
from datetime import datetime, timedelta

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import text

from . import db
from .config import settings

_bearer = HTTPBearer(auto_error=False)
PBKDF2_ITERATIONS = 310_000


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, PBKDF2_ITERATIONS)
    return f"pbkdf2_sha256${PBKDF2_ITERATIONS}${salt.hex()}${dk.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        _, iterations, salt_hex, hash_hex = stored.split("$")
        dk = hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt_hex), int(iterations))
        return hmac.compare_digest(dk.hex(), hash_hex)
    except (ValueError, TypeError):
        return False


def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def issue_token(conn, user_id: int, label: str = "extension") -> str:
    token = secrets.token_urlsafe(32)
    conn.execute(text(
        "INSERT INTO api_tokens (user_id, token_hash, label, expires_at) VALUES (:u, :h, :l, :e)"
    ), {"u": user_id, "h": _token_hash(token), "l": label[:64],
        "e": datetime.utcnow() + timedelta(days=settings.token_ttl_days)})
    return token


def revoke_token(conn, token: str) -> None:
    conn.execute(text("DELETE FROM api_tokens WHERE token_hash = :h"), {"h": _token_hash(token)})


def audit(conn, user_id: int | None, event: str, detail: str | None = None) -> None:
    conn.execute(text("INSERT INTO audit_events (user_id, event, detail) VALUES (:u, :e, :d)"),
                 {"u": user_id, "e": event, "d": (detail or "")[:255]})


def current_user(creds: HTTPAuthorizationCredentials | None = Depends(_bearer)) -> dict:
    if creds is None or creds.scheme.lower() != "bearer":
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Not authenticated",
                            headers={"WWW-Authenticate": "Bearer"})
    with db.connect() as conn:
        row = conn.execute(text(
            "SELECT u.id, u.email FROM api_tokens t JOIN users u ON u.id = t.user_id "
            "WHERE t.token_hash = :h AND t.expires_at > UTC_TIMESTAMP()"
        ), {"h": _token_hash(creds.credentials)}).fetchone()
        if row is None:
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid or expired token",
                                headers={"WWW-Authenticate": "Bearer"})
        conn.execute(text("UPDATE api_tokens SET last_used_at = UTC_TIMESTAMP() WHERE token_hash = :h"),
                     {"h": _token_hash(creds.credentials)})
    return {"id": row[0], "email": row[1], "token": creds.credentials}
