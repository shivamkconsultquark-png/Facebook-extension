"""Server-side Facebook auto-scraper — the counterpart of the X/Twitter browser scraper.

A backend-controlled Chrome (Playwright, driving the installed Chrome) opens the user's
Facebook home feed with their imported session, scrolls it, and hands the posts to the
scoring engine every `interval_seconds`. One worker thread per dashboard user.

Session sources (same two options as the Twitter page):
  1. Import: Cookie-Editor "Export as JSON" pasted into the dashboard (works on servers).
  2. Login window: a visible Chrome window where the user logs in once (needs a desktop
     on the machine running the backend; see SCRAPER_ALLOW_LOGIN_WINDOW).

No anti-detection tricks are used: if Facebook shows a login or security checkpoint, the
scraper reports "login required" and waits for a fresh session instead of working around it.
"""
from __future__ import annotations

import json
import logging
import pathlib
import random
import threading
import time
from datetime import datetime, timedelta, timezone

from sqlalchemy import text

from ... import db, repository as repo, scoring
from ...config import settings
from . import session as fb_session

log = logging.getLogger("fbshield.scraper")

HOME_URL = "https://www.facebook.com/"
LOGIN_URL = "https://www.facebook.com/login"
_JS_DIR = pathlib.Path(__file__).parent / "js"
_STATE_KEY = "scraper_state"
_browser_slots = threading.BoundedSemaphore(max(1, settings.scraper_max_browsers))


def _page_scripts() -> str:
    return "\n;\n".join((_JS_DIR / f).read_text(encoding="utf-8") for f in ("shared.js", "extractor.js", "collect.js", "reels.js"))


def _iso(dt: datetime | None) -> str | None:
    return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z") if dt else None


def _interval_label(seconds: int) -> str:
    if seconds % 3600 == 0:
        h = seconds // 3600
        return f"{h} hour{'s' if h != 1 else ''}"
    m = max(1, seconds // 60)
    return f"{m} minute{'s' if m != 1 else ''}"


def _ai_scoring() -> dict:
    """Gemini health for the status box: is it on, and did the last call work?"""
    from ...detection import llm
    s = llm.status()
    failing = bool(s["last_error_at"]) and (not s["last_ok_at"] or s["last_error_at"] > s["last_ok_at"])
    return {"enabled": s["enabled"], "model": s["model"] or s["configured_model"],
            "working": None if not (s["ok"] or s["failed"]) else not failing,
            "last_error": s["last_error"] if failing else None}


def profile_url(account_id: str | None) -> str | None:
    return f"https://www.facebook.com/profile.php?id={account_id}" if account_id and account_id.isdigit() else None


# ======================================================================= browser layer
# Each function opens and closes its own Playwright browser so it can run in any thread.

class BrowserUnavailable(RuntimeError):
    pass


def _launch(p, headless: bool):
    kwargs = {"headless": headless, "args": ["--disable-notifications"]}
    if settings.scraper_browser_channel:
        kwargs["channel"] = settings.scraper_browser_channel
    try:
        return p.chromium.launch(**kwargs)
    except Exception as e:  # browser not installed / no display
        raise BrowserUnavailable(
            f"Could not start {settings.scraper_browser_channel or 'Chromium'}: {str(e).splitlines()[0]}") from e


def _fb_cookies(context) -> list[dict]:
    return [c for c in context.cookies() if "facebook.com" in c.get("domain", "")]


def _logged_in(page, context) -> tuple[bool, str | None]:
    """(logged_in, reason_if_not)."""
    url = page.url
    if "/checkpoint" in url:
        return False, "Facebook asked for a security check on this session."
    names = {c["name"] for c in _fb_cookies(context)}
    if "/login" in url or "c_user" not in names or "xs" not in names:
        return False, "The Facebook session is logged out or expired."
    if page.locator('form[action*="login"] input[name="email"]').count() > 0:
        return False, "The Facebook session is logged out or expired."
    return True, None


def _account_name(page, account_id: str | None) -> str | None:
    """Best effort: the user's name as shown in Facebook's own navigation."""
    try:
        name = page.evaluate("""(id) => {
            const links = [...document.querySelectorAll('a[href]')];
            const own = links.find(a => id && a.href.includes(id) && (a.textContent || '').trim().length > 1);
            if (own) return own.textContent.trim();
            const prof = document.querySelector('[aria-label="Your profile"], [aria-label="Your Profile"]');
            return prof ? (prof.getAttribute('aria-label') === 'Your profile' ? null : prof.textContent.trim()) : null;
        }""", account_id)
        if name and 1 < len(name) < 120:
            return name
        page.goto("https://www.facebook.com/me", wait_until="domcontentloaded", timeout=30000)
        title = (page.title() or "").replace("| Facebook", "").strip()
        return title if title and title.lower() != "facebook" else None
    except Exception:
        return None


def browse_feed(cookies: list[dict], want_name: bool = False, skip_albums: set | None = None) -> dict:
    """Open the home feed with `cookies`, scroll it, and return what was rendered."""
    from playwright.sync_api import sync_playwright

    with _browser_slots, sync_playwright() as p:
        browser = _launch(p, settings.scraper_headless)
        try:
            context = browser.new_context(viewport={"width": 1280, "height": 1000}, locale="en-US")
            context.add_cookies(cookies)
            # Init scripts run in the page's main world and are not blocked by Facebook's CSP.
            context.add_init_script(script=_page_scripts())
            page = context.new_page()
            page.goto(HOME_URL, wait_until="domcontentloaded", timeout=45000)
            page.wait_for_timeout(3000)
            ok, reason = _logged_in(page, context)
            if not ok:
                return {"logged_in": False, "reason": reason, "posts": []}

            # Facebook unloads posts that scroll out of view, so collect after every short
            # scroll (about one post) rather than jumping and missing posts in between.
            merged: dict[str, dict] = {}
            idle = 0
            for _ in range(max(1, settings.scraper_scrolls)):
                before = len(merged)
                page.evaluate("() => window.FBShieldReveal()")
                page.wait_for_timeout(700)  # permalinks + "See more" text render after the hover/click
                _merge(merged, page.evaluate("n => window.FBShieldCollect(n)", settings.scraper_max_posts)["posts"])
                if len(merged) >= settings.scraper_max_posts:
                    break
                idle = idle + 1 if len(merged) == before else 0
                if idle >= 4 or (idle >= 2 and _feed_ended(page)):
                    break
                page.mouse.wheel(0, random.randint(900, 1300))
                page.wait_for_timeout(random.randint(1200, 2000))
            page.evaluate("() => window.FBShieldReveal()")
            page.wait_for_timeout(1500)  # let "See more" expansions finish
            _merge(merged, page.evaluate("n => window.FBShieldCollect(n)", settings.scraper_max_posts)["posts"])

            posts = list(merged.values())[: settings.scraper_max_posts]
            _fetch_albums(context, posts, skip_albums or set())
            name = _account_name(page, fb_session.account_id(_fb_cookies(context))) if want_name else None
            posts += _browse_reels(page, settings.scraper_reels, {p["post"].get("post_id") for p in posts})
            return {"logged_in": True, "posts": posts, "cookies": _fb_cookies(context), "account_name": name}
        finally:
            browser.close()


# Largest photo on the page = the one the photo viewer is showing.
_VIEWER_PHOTO = """() => {
    const imgs = [...document.querySelectorAll('img[src*="scontent"]')].map(i => {
        const r = i.getBoundingClientRect(); return [r.width * r.height, i.src]; });
    imgs.sort((a, b) => b[0] - a[0]);
    return imgs.length && imgs[0][0] > 40000 ? imgs[0][1] : null;
}"""


def _album_photos(context, url: str, expected: int) -> tuple[list[str], bool]:
    """Open Facebook's photo viewer at the post's first photo and press "Next photo" until it
    wraps around, collecting each photo. The feed only renders 5 tiles of an album."""
    page = context.new_page()
    photos: list[str] = []
    wrapped = False
    try:
        page.goto(url, wait_until="domcontentloaded", timeout=30000)
        page.wait_for_timeout(2500)
        cur = page.evaluate(_VIEWER_PHOTO)
        limit = min(expected, settings.scraper_album_max)
        while cur and cur not in photos and len(photos) < limit:
            photos.append(cur)
            nxt = page.locator('[aria-label="Next photo"]')
            if len(photos) >= limit or not nxt.count():
                break
            nxt.first.click(timeout=5000)
            new = cur
            for _ in range(40):  # up to ~6 s for the next photo to load
                page.wait_for_timeout(150)
                new = page.evaluate(_VIEWER_PHOTO)
                if new and new != cur:
                    break
            cur = new
        wrapped = bool(cur) and cur in photos  # back at the first photo: the whole album was seen
    except Exception as e:
        log.warning("album viewer stopped early: %s", str(e).splitlines()[0][:200])
    finally:
        page.close()
    return photos, wrapped or len(photos) >= expected


def _fetch_albums(context, posts: list[dict], skip: set) -> None:
    """For posts showing "+N", replace the 5 feed tiles with every photo of the album.
    Albums already stored in full (`skip`, by post id) are not opened again."""
    todo = [p for p in posts if p.get("media_more") and p.get("album_url")
            and p["post"].get("post_id") not in skip][: settings.scraper_max_albums]
    for item in todo:
        expected = len(item.get("media_urls") or []) + item["media_more"]
        photos, complete = _album_photos(context, item["album_url"], expected)
        if len(photos) > len(item.get("media_urls") or []):
            item["media_urls"] = photos
            item["media_url"] = photos[0]
            # Not stored: 0 when the viewer went all the way round, else what the "+N" says is left
            # (also the case when an album is longer than SCRAPER_ALBUM_MAX).
            item["media_more"] = 0 if complete and len(photos) < settings.scraper_album_max else max(0, expected - len(photos))
            item["album_complete"] = True


REELS_URL = "https://www.facebook.com/reel/"


def _browse_reels(page, limit: int, known_ids: set) -> list[dict]:
    """Watch the Reels tab: read the playing reel, press "Next card", repeat `limit` times.
    Best effort — a failure here never loses the feed posts already collected."""
    if limit <= 0:
        return []
    reels: dict[str, dict] = {}
    try:
        page.goto(REELS_URL, wait_until="domcontentloaded", timeout=45000)
        page.wait_for_timeout(4000)
        if "/reel" not in page.url:
            return []
        idle = 0
        for _ in range(limit * 2):
            before = len(reels)
            for item in page.evaluate("() => window.FBShieldCollectReels()")["posts"]:
                rid = item["post"]["post_id"]
                if rid not in known_ids and (rid not in reels or len(item["post"]["text"]) > len(reels[rid]["post"]["text"])):
                    reels[rid] = item
            if len(reels) >= limit:
                break
            idle = idle + 1 if len(reels) == before else 0
            if idle >= 3:
                break
            nxt = page.locator('[aria-label="Next card"][role="button"]')
            if nxt.count():
                nxt.first.click(timeout=5000)
            else:
                page.keyboard.press("ArrowDown")
            page.wait_for_timeout(random.randint(2200, 3200))
    except Exception as e:
        log.warning("reels pass stopped early: %s", str(e).splitlines()[0][:200])
    return list(reels.values())[:limit]


def _feed_ended(page) -> bool:
    """Facebook's "No more posts" card (accounts with few friends/pages run out quickly)."""
    try:
        return page.get_by_text("No more posts", exact=True).count() > 0
    except Exception:
        return False


_EXTRA = ("media_url", "author_image", "reactions_count", "comments_count", "shares_count")
_SAME_PREFIX = 60


def _same_post(a: dict, b: dict) -> bool:
    """Same post seen twice: same id, or same author and the same opening text.
    (The id can change once the hovered permalink appears, and the text grows after "See more".)"""
    if a.get("post_id") and a.get("post_id") == b.get("post_id"):
        return True
    if a.get("author_name") != b.get("author_name"):
        return False
    ta, tb = a.get("text") or "", b.get("text") or ""
    n = min(_SAME_PREFIX, len(ta), len(tb))
    return n >= 15 and ta[:n] == tb[:n]


def _merge(merged: dict, posts: list[dict]) -> None:
    """Dedupe across scrolls. Keeps the longest text, the latest post link (taken after the
    hover revealed it) and any image/avatar/count that one sighting had and the other missed."""
    for item in posts:
        post = item["post"]
        key = next((k for k, v in merged.items() if _same_post(v["post"], post)), None)
        if key is None:
            merged[post.get("post_id") or post.get("permalink") or f"{post.get('author_name')}|{(post.get('text') or '')[:200]}"] = item
            continue
        prev = merged[key]
        longer = item if len(post.get("text") or "") > len(prev["post"].get("text") or "") else prev
        out = {**longer, "post": dict(longer["post"])}
        if post.get("post_id"):
            out["post"]["post_id"], out["post"]["permalink"] = post["post_id"], post.get("permalink")
        for f in _EXTRA:
            out[f] = item.get(f) or prev.get(f)
        # Lazy-loaded photo tiles: keep whichever sighting had more of them.
        imgs = max((item.get("media_urls") or [], prev.get("media_urls") or []), key=len)
        out["media_urls"] = imgs
        out["media_url"] = imgs[0] if imgs else out.get("media_url")
        out["media_more"] = item.get("media_more") or prev.get("media_more") or 0
        out["media_type"] = item.get("media_type") or prev.get("media_type")
        merged[key] = out


def verify_session(cookies: list[dict]) -> dict:
    """Check imported cookies actually log in; returns {"ok", "reason", "account_name", "cookies"}."""
    from playwright.sync_api import sync_playwright

    with _browser_slots, sync_playwright() as p:
        browser = _launch(p, True)
        try:
            context = browser.new_context(viewport={"width": 1280, "height": 1000}, locale="en-US")
            context.add_cookies(cookies)
            page = context.new_page()
            page.goto(HOME_URL, wait_until="domcontentloaded", timeout=45000)
            page.wait_for_timeout(2500)
            ok, reason = _logged_in(page, context)
            if not ok:
                return {"ok": False, "reason": reason}
            fresh = _fb_cookies(context)
            return {"ok": True, "cookies": fresh, "account_name": _account_name(page, fb_session.account_id(fresh))}
        finally:
            browser.close()


def login_window(timeout_s: int, cancelled: threading.Event) -> dict:
    """Open a visible Chrome window on facebook.com/login and wait for the user to sign in."""
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser = _launch(p, False)
        try:
            context = browser.new_context(viewport={"width": 1100, "height": 850}, locale="en-US")
            page = context.new_page()
            page.goto(LOGIN_URL, wait_until="domcontentloaded", timeout=45000)
            deadline = time.monotonic() + timeout_s
            while time.monotonic() < deadline and not cancelled.is_set():
                if page.is_closed():
                    return {"ok": False, "reason": "The login window was closed before signing in."}
                names = {c["name"] for c in _fb_cookies(context)}
                if {"c_user", "xs"} <= names and "/login" not in page.url and "/checkpoint" not in page.url:
                    page.wait_for_timeout(2000)
                    fresh = _fb_cookies(context)
                    return {"ok": True, "cookies": fresh, "account_name": _account_name(page, fb_session.account_id(fresh))}
                page.wait_for_timeout(1500)
            return {"ok": False, "reason": "Timed out waiting for Facebook login." if not cancelled.is_set() else "Cancelled."}
        finally:
            try:
                browser.close()
            except Exception:
                pass


# ======================================================================= workers

class Worker(threading.Thread):
    """Scrapes one user's feed every interval until stopped."""

    def __init__(self, manager: "ScraperManager", user_id: int, interval: int):
        super().__init__(daemon=True, name=f"fb-scraper-{user_id}")
        self.manager, self.user_id, self.interval = manager, user_id, interval
        self.stop_event, self.wake_event = threading.Event(), threading.Event()
        self.lock = threading.Lock()
        self.state = {"running": True, "scraping": False, "needs_login": False, "message": "Starting…",
                      "error": None, "next_run_at": None, "cycles": 0, "last_count": 0,
                      "total_scraped": 0, "last_run": None, "last_videos": 0}

    def update(self, **kw):
        with self.lock:
            self.state.update(kw)

    def snapshot(self) -> dict:
        with self.lock:
            return dict(self.state)

    def run(self):
        while not self.stop_event.is_set():
            try:
                self.cycle()
            except Exception as e:  # e.g. MySQL unreachable: keep the worker alive and retry next cycle
                log.exception("scrape cycle failed for user %s", self.user_id)
                self.update(scraping=False, error=str(e)[:300],
                            message="❌ Scrape failed — will retry next cycle.")
            nxt = datetime.now(timezone.utc) + timedelta(seconds=self.interval)
            self.update(next_run_at=_iso(nxt))
            self.wake_event.wait(self.interval)
            self.wake_event.clear()
        self.update(running=False, scraping=False, next_run_at=None, message="Stopped.")

    def cycle(self):
        with db.connect() as conn:
            cookies, acct = fb_session.load(conn, self.user_id)
        if not cookies:
            self.update(needs_login=True, scraping=False, error=None,
                        message="Login required — import your Facebook session below to link the account.")
            return
        self.update(scraping=True, error=None, message="Scraping your Facebook feed…")
        try:
            with db.connect() as conn:
                done = repo.complete_album_ids(conn, self.user_id)
            res = self.manager.browse(cookies, want_name=not (acct and acct.get("name")), skip_albums=done)
        except BrowserUnavailable as e:
            self.update(scraping=False, error=str(e), message=f"❌ {e}")
            return
        except Exception as e:
            log.exception("scrape failed for user %s", self.user_id)
            self.update(scraping=False, error=str(e)[:300], message="❌ Scrape failed — will retry next cycle.")
            return
        if self.stop_event.is_set():
            return
        if not res["logged_in"]:
            self.update(scraping=False, needs_login=True,
                        message=f"Login required — {res.get('reason') or 'session expired'} Import a fresh session below.")
            return
        with db.connect() as conn:
            fb_session.save(conn, self.user_id, res["cookies"], res.get("account_name"))
        stored = scoring.store_posts(self.user_id, res["posts"])
        with self.lock:
            self.state["cycles"] += 1
            self.state["total_scraped"] += stored["new"]
            self.state.update(scraping=False, needs_login=False, last_count=stored["stored"], last_videos=stored["videos"],
                              last_run=_iso(datetime.now(timezone.utc)),
                              message=f"✅ Running — scraping your Facebook feed every {_interval_label(self.interval)}")
        log.info("user %s: scraped %s posts (%s new)", self.user_id, stored["stored"], stored["new"])


class ScraperManager:
    def __init__(self, browse=browse_feed, verify=verify_session, login=login_window):
        # Browser functions are injectable so the orchestration can be tested without Chrome.
        self.browse, self.verify, self.login = browse, verify, login
        self._workers: dict[int, Worker] = {}
        self._login: dict[int, dict] = {}  # user_id -> {"thread", "cancel", "message"}
        self._lock = threading.Lock()

    # ---------- persisted run state (so scrapers resume after a server restart)
    def _save_state(self, user_id: int, running: bool, interval: int):
        with db.connect() as conn:
            repo.set_setting(conn, user_id, _STATE_KEY, json.dumps({"running": running, "interval_seconds": interval}))

    def _load_state(self, user_id: int) -> dict:
        with db.connect() as conn:
            raw = repo.get_setting(conn, user_id, _STATE_KEY)
        try:
            return json.loads(raw) if raw else {}
        except ValueError:
            return {}

    def resume_in_background(self, stop: threading.Event, max_delay: float = 60.0) -> threading.Thread:
        """Resume scrapers, retrying while the database is unreachable (e.g. MySQL still starting after a reboot)."""
        def run():
            delay = 2.0
            while not stop.is_set():
                try:
                    self.resume_all()
                    return
                except Exception as e:
                    log.warning("could not resume Facebook scrapers yet (%s); retrying in %.0f s", e, delay)
                    stop.wait(delay)
                    delay = min(delay * 2, max_delay)
        t = threading.Thread(target=run, daemon=True, name="fb-resume")
        t.start()
        return t

    def resume_all(self):
        if not settings.scraper_resume:
            return
        with db.connect() as conn:
            rows = conn.execute(text(
                "SELECT user_id, setting_value FROM user_settings WHERE setting_key = :k"), {"k": _STATE_KEY}).fetchall()
        for user_id, raw in rows:
            try:
                st = json.loads(raw)
            except ValueError:
                continue
            if st.get("running"):
                log.info("resuming Facebook scraper for user %s", user_id)
                self._spawn(user_id, int(st.get("interval_seconds") or settings.scraper_default_interval))

    # ---------- commands
    def _spawn(self, user_id: int, interval: int):
        with self._lock:
            w = self._workers.get(user_id)
            if w and w.is_alive():
                return w
            w = Worker(self, user_id, interval)
            self._workers[user_id] = w
            w.start()
            return w

    def start(self, user_id: int, interval: int) -> None:
        with db.connect() as conn:
            linked = fb_session.account(conn, user_id) is not None
        if not linked:
            if settings.scraper_allow_login_window:
                self.open_login_window(user_id, then_start=interval)
                return
            raise ValueError("Link your Facebook account first — import your session below.")
        self._save_state(user_id, True, interval)
        self._spawn(user_id, interval)

    def stop(self, user_id: int) -> None:
        with self._lock:
            w = self._workers.pop(user_id, None)
        if w:
            w.stop_event.set()
            w.wake_event.set()
        login = self._login.get(user_id)
        if login:
            login["cancel"].set()
        self._save_state(user_id, False, self._load_state(user_id).get("interval_seconds") or settings.scraper_default_interval)

    def switch_account(self, user_id: int) -> None:
        self.stop(user_id)
        with db.connect() as conn:
            fb_session.delete(conn, user_id)

    def import_session(self, user_id: int, raw: str) -> dict:
        cookies = fb_session.parse_cookies(raw)  # raises CookieError with a user-facing message
        try:
            check = self.verify(cookies)
        except BrowserUnavailable as e:
            return {"ok": False, "detail": f"Session looks valid but could not be checked: {e}"}
        if not check["ok"]:
            return {"ok": False, "detail": f"{check['reason']} Log into facebook.com in your browser, then export again."}
        with db.connect() as conn:
            fb_session.save(conn, user_id, check["cookies"], check.get("account_name"))
        self._wake(user_id)
        who = check.get("account_name") or f"account {fb_session.account_id(check['cookies'])}"
        return {"ok": True, "detail": f"Session imported — linked to {who}."
                + (" Scraping resumes automatically." if self._running(user_id) else " Click Start to begin scraping.")}

    def open_login_window(self, user_id: int, then_start: int | None = None) -> None:
        if not settings.scraper_allow_login_window:
            raise ValueError("The login window is disabled on this server — import your session instead.")
        if user_id in self._login and self._login[user_id]["thread"].is_alive():
            return
        cancel = threading.Event()
        entry = {"cancel": cancel, "message": "A Chrome window opened — log in to Facebook there."}

        def run():
            try:
                res = self.login(settings.scraper_login_timeout, cancel)
            except BrowserUnavailable as e:
                res = {"ok": False, "reason": str(e)}
            except Exception as e:
                log.exception("login window failed")
                res = {"ok": False, "reason": str(e)[:200]}
            if res["ok"]:
                with db.connect() as conn:
                    fb_session.save(conn, user_id, res["cookies"], res.get("account_name"))
                entry["message"] = None
                if then_start:
                    self._save_state(user_id, True, then_start)
                    self._spawn(user_id, then_start)
                self._wake(user_id)
            else:
                entry["message"] = f"Login window: {res['reason']}"

        entry["thread"] = threading.Thread(target=run, daemon=True, name=f"fb-login-{user_id}")
        self._login[user_id] = entry
        entry["thread"].start()

    def _wake(self, user_id: int):
        w = self._workers.get(user_id)
        if w and w.is_alive():
            w.wake_event.set()

    def _running(self, user_id: int) -> bool:
        w = self._workers.get(user_id)
        return bool(w and w.is_alive())

    # ---------- status (same fields the AutoScraper page reads)
    def status(self, user_id: int) -> dict:
        with db.connect() as conn:
            acct = fb_session.account(conn, user_id)
        saved = self._load_state(user_id)
        interval = saved.get("interval_seconds") or settings.scraper_default_interval
        w = self._workers.get(user_id)
        login = self._login.get(user_id)
        login_open = bool(login and login["thread"].is_alive())

        if w and w.is_alive():
            st = w.snapshot()
            interval = w.interval
        else:
            st = {"running": False, "scraping": False, "needs_login": False, "error": None, "next_run_at": None,
                  "cycles": 0, "last_count": 0, "total_scraped": 0, "last_run": None, "last_videos": 0,
                  "message": (f"Stopped. Click Start to scrape your Facebook feed every {_interval_label(interval)}."
                              if acct else "Not linked yet — import your Facebook session below, then click Start.")}
        if login_open:
            st["needs_login"] = True
            st["message"] = login["message"]
        elif login and login.get("message") and not st["running"]:
            st["message"] = login["message"]
        st.update({
            "interval_seconds": interval,
            "fb_account": (acct.get("name") or f"ID {acct.get('id')}") if acct else None,
            "fb_account_url": profile_url(acct.get("id")) if acct else None,
            "login_window_open": login_open,
            "login_window_available": settings.scraper_allow_login_window,
            "ai_scoring": _ai_scoring(),
        })
        return st

    def health(self) -> dict:
        """Scraper threads that should be running, and any that died anyway (shown by /health)."""
        with self._lock:
            workers = list(self._workers.values())
        live = [w for w in workers if w.is_alive() and not w.stop_event.is_set()]
        dead = [w.user_id for w in workers if not w.is_alive() and not w.stop_event.is_set()]
        return {"running": len(live), "dead_workers": dead}

    def shutdown(self):
        for w in list(self._workers.values()):
            w.stop_event.set()
            w.wake_event.set()
        for entry in self._login.values():
            entry["cancel"].set()


manager = ScraperManager()
