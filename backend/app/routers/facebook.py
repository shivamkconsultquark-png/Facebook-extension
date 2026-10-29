"""/facebook/* — the Facebook counterpart of the Twitter page's /twitter/* endpoints.

  GET  /facebook/feed                              ← /twitter/feed
  GET  /facebook/config                            ← /twitter/config
  GET/POST /facebook/settings/llm-instructions     ← /twitter/settings/llm-instructions
  GET  /facebook/browser-scraper/status            ← /twitter/browser-scraper/status
  POST /facebook/browser-scraper/start|stop|switch-account|import-session
  POST /facebook/browser-scraper/login-window      (opens a visible Chrome login window)
"""
from __future__ import annotations

from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field

from .. import db, repository as repo
from ..auth import audit, current_user
from ..detection import llm
from ..platforms.facebook import embed as fb_embed
from ..platforms.facebook import session as fb_session
from ..platforms.facebook.scraper import manager

router = APIRouter(prefix="/facebook", tags=["facebook"])

# Same choices as the AutoScraper interval dropdown (2 minutes … 24 hours).
ALLOWED_INTERVALS = {120, 180, 300, 600, 900, 1800, 3600, 7200, 14400, 28800, 43200, 86400}


# ---------- timeline
def _check_date_range(date_from: str | None, date_to: str | None) -> None:
    """The regex only checks the shape: also reject impossible days (2026-02-31) and reversed ranges."""
    parsed = {}
    for name, value in (("date_from", date_from), ("date_to", date_to)):
        if value:
            try:
                parsed[name] = date.fromisoformat(value)
            except ValueError:
                raise HTTPException(422, f"{name} is not a real date") from None
    if len(parsed) == 2 and parsed["date_from"] > parsed["date_to"]:
        raise HTTPException(422, "date_from must be on or before date_to")


@router.get("/feed")
def feed(
    page: int = Query(1, ge=1, le=100_000),
    limit: int = Query(10, ge=1, le=50),
    category: str | None = Query(None, max_length=32),
    search: str | None = Query(None, max_length=200),
    date_from: str | None = Query(None, pattern=r"^\d{4}-\d{2}-\d{2}$"),
    date_to: str | None = Query(None, pattern=r"^\d{4}-\d{2}-\d{2}$"),
    hide_above: int | None = Query(None, ge=0, le=100),
    sort: str = Query("newest", pattern="^(newest|oldest|score_desc|score_asc)$"),
    media: str | None = Query(None, pattern="^(all|posts|videos|reels)$"),
    user: dict = Depends(current_user),
):
    if category and category not in repo.CATEGORY_ALIASES:
        raise HTTPException(422, f"Unknown category '{category}'")
    _check_date_range(date_from, date_to)
    with db.connect() as conn:
        return repo.feed(conn, user["id"], page, limit, sort, category=category, search=search,
                         date_from=date_from, date_to=date_to, hide_above=hide_above, media=media)


@router.get("/feed/{post_id}/playable")
def video_playable(post_id: int, user: dict = Depends(current_user)):
    """Whether Facebook's embedded player can play this post's video (otherwise it shows "Video unavailable")."""
    with db.connect() as conn:
        url = repo.video_url(conn, user["id"], post_id)
    if not url:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No video for this post")
    return {"playable": fb_embed.playable(url)}


@router.get("/config")
def config(user: dict = Depends(current_user)):
    with db.connect() as conn:
        return {"config": repo.stats(conn, user["id"])}


# ---------- LLM instructions (same contract as /twitter/settings/llm-instructions)
class InstructionsBody(BaseModel):
    instructions: str | None = Field(None, max_length=8000)
    restore_default: bool = False


@router.get("/settings/llm-instructions")
def get_instructions(user: dict = Depends(current_user)):
    with db.connect() as conn:
        custom = repo.get_setting(conn, user["id"], "llm_instructions")
    return {"instructions": custom or llm.DEFAULT_INSTRUCTIONS, "is_default": not custom,
            "llm_enabled": llm.available()}


@router.post("/settings/llm-instructions")
def set_instructions(body: InstructionsBody, user: dict = Depends(current_user)):
    if not body.restore_default and not (body.instructions or "").strip():
        raise HTTPException(422, "Instructions cannot be empty")
    with db.connect() as conn:
        value = None if body.restore_default else body.instructions.strip()
        repo.set_setting(conn, user["id"], "llm_instructions", value)
        audit(conn, user["id"], "llm_instructions_" + ("restored" if value is None else "updated"))
    return {"instructions": value or llm.DEFAULT_INSTRUCTIONS, "is_default": value is None}


# ---------- browser scraper (same contract as /twitter/browser-scraper/*)
class StartBody(BaseModel):
    interval_seconds: int = 300


class ImportBody(BaseModel):
    cookies: str = Field(..., max_length=200_000)


@router.get("/browser-scraper/status")
def scraper_status(user: dict = Depends(current_user)):
    return manager.status(user["id"])


@router.post("/browser-scraper/start")
def scraper_start(body: StartBody | None = None, user: dict = Depends(current_user)):
    interval = (body.interval_seconds if body else 300)
    if interval not in ALLOWED_INTERVALS:
        raise HTTPException(422, "Unsupported interval")
    try:
        manager.start(user["id"], interval)
    except ValueError as e:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(e))
    with db.connect() as conn:
        audit(conn, user["id"], "fb_scraper_start", f"interval={interval}")
    return manager.status(user["id"])


@router.post("/browser-scraper/stop")
def scraper_stop(user: dict = Depends(current_user)):
    manager.stop(user["id"])
    with db.connect() as conn:
        audit(conn, user["id"], "fb_scraper_stop")
    return manager.status(user["id"])


@router.post("/browser-scraper/switch-account")
def scraper_switch_account(user: dict = Depends(current_user)):
    manager.switch_account(user["id"])
    with db.connect() as conn:
        audit(conn, user["id"], "fb_session_unlinked")
    return manager.status(user["id"])


@router.post("/browser-scraper/import-session")
def scraper_import_session(body: ImportBody, user: dict = Depends(current_user)):
    try:
        result = manager.import_session(user["id"], body.cookies)
    except fb_session.CookieError as e:
        return {"ok": False, "detail": str(e)}
    with db.connect() as conn:
        audit(conn, user["id"], "fb_session_imported" if result["ok"] else "fb_session_import_failed")
    return result


@router.post("/browser-scraper/login-window")
def scraper_login_window(user: dict = Depends(current_user)):
    try:
        manager.open_login_window(user["id"])
    except ValueError as e:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(e))
    return manager.status(user["id"])
