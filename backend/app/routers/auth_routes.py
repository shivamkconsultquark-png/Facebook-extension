from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from .. import db
from ..auth import audit, current_user, hash_password, issue_token, revoke_token, verify_password
from ..config import settings
from ..ratelimit import SlidingWindowLimiter

router = APIRouter(prefix="/auth", tags=["auth"])
_login_limiter = SlidingWindowLimiter(limit=10, window_seconds=300)


class Credentials(BaseModel):
    email: str = Field(..., min_length=3, max_length=255)
    password: str = Field(..., min_length=8, max_length=200)
    label: str = Field("extension", max_length=64)

    @field_validator("email")
    @classmethod
    def _email(cls, v: str) -> str:
        v = v.strip().lower()
        if "@" not in v or v.startswith("@") or v.endswith("@"):
            raise ValueError("invalid email")
        return v


@router.post("/register", status_code=201)
def register(body: Credentials):
    if not settings.allow_registration:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Registration is disabled on this server")
    try:
        with db.connect() as conn:
            res = conn.execute(text("INSERT INTO users (email, password_hash) VALUES (:e, :p)"),
                               {"e": body.email, "p": hash_password(body.password)})
            user_id = res.lastrowid
            token = issue_token(conn, user_id, body.label)
            audit(conn, user_id, "register")
    except IntegrityError:
        raise HTTPException(status.HTTP_409_CONFLICT, "An account with this email already exists")
    return {"token": token, "user": {"id": user_id, "email": body.email}}


@router.post("/login")
def login(body: Credentials):
    allowed, retry = _login_limiter.try_acquire(body.email)
    if not allowed:
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, "Too many login attempts",
                            headers={"Retry-After": str(int(retry))})
    with db.connect() as conn:
        row = conn.execute(text("SELECT id, password_hash FROM users WHERE email = :e"), {"e": body.email}).fetchone()
        ok = row is not None and verify_password(body.password, row[1])
        if not ok:
            # Committed before the 401: raising inside the block would roll the audit row back.
            audit(conn, row[0] if row else None, "login_failed", body.email)
        else:
            token = issue_token(conn, row[0], body.label)
            audit(conn, row[0], "login", body.label)
    if not ok:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Incorrect email or password")
    return {"token": token, "user": {"id": row[0], "email": body.email}}


@router.get("/me")
def me(user: dict = Depends(current_user)):
    return {"id": user["id"], "email": user["email"]}


@router.post("/logout")
def logout(user: dict = Depends(current_user)):
    with db.connect() as conn:
        revoke_token(conn, user["token"])
        audit(conn, user["id"], "logout")
    return {"ok": True}
