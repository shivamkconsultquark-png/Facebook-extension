#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Check the Facebook auto-scraper configuration: database, browser, sessions, running state.
Facebook counterpart of the Twitter backend's check_twitter_config.py.
"""
import json
import sys

sys.stdout.reconfigure(encoding='utf-8')

from sqlalchemy import text  # noqa: E402

from app import db  # noqa: E402
from app.config import settings  # noqa: E402
from app.detection import llm  # noqa: E402

print("=" * 70)
print("FACEBOOK AUTO-SCRAPER CONFIGURATION CHECK")
print("=" * 70)

print(f"\nDatabase:       {'✅ connected' if db.ping() else '❌ NOT reachable — check DB_* in .env'}")
if llm.available():
    # A real call: a configured key can still fail (timeouts, overloaded or retired models).
    probe = llm.classify("Congratulations, you won a car! Pay a small delivery fee to claim it.", [], "Prize Desk")
    if probe:
        print(f"AI scoring:     ✅ Gemini ({probe['model']}) answered — test scam score {probe['score']}")
    else:
        print(f"AI scoring:     ❌ Gemini configured but failing — {llm.status()['last_error']}")
        print("   Posts are scored with rules only until this works. Try GEMINI_MODEL=gemini-flash-lite-latest.")
else:
    print("AI scoring:     ⚠ rules only (no GEMINI_API_KEY)")
print(f"Browser:        {settings.scraper_browser_channel or 'bundled Chromium'} · headless={settings.scraper_headless}")
print(f"Login window:   {'enabled' if settings.scraper_allow_login_window else 'disabled (session import only)'}")

try:
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        kw = {"headless": True}
        if settings.scraper_browser_channel:
            kw["channel"] = settings.scraper_browser_channel
        p.chromium.launch(**kw).close()
    print("Browser launch: ✅ OK")
except Exception as e:
    print(f"Browser launch: ❌ {str(e).splitlines()[0]}")
    print("   Install Google Chrome, or set SCRAPER_BROWSER_CHANNEL= and run: python -m playwright install chromium")

with db.connect() as conn:
    sessions = conn.execute(text(
        "SELECT s.user_id, u.email, s.account_name, s.account_id, s.updated_at FROM fb_sessions s JOIN users u ON u.id = s.user_id"
    )).fetchall()
    states = {r[0]: json.loads(r[1]) for r in conn.execute(text(
        "SELECT user_id, setting_value FROM user_settings WHERE setting_key = 'scraper_state'"))}

print(f"\nLinked Facebook sessions: {len(sessions)}")
for user_id, email, name, acct_id, updated in sessions:
    st = states.get(user_id, {})
    run = f"✅ RUNNING every {st.get('interval_seconds')}s" if st.get("running") else "⏹ stopped"
    print(f"   - {email}: {name or 'account ' + str(acct_id)} · session updated {updated} · {run}")

print("=" * 70)
if sessions:
    print("✅ AUTO-SCRAPER IS CONFIGURED")
    print("   Running scrapers resume automatically when the backend starts.")
else:
    print("❌ NO FACEBOOK ACCOUNT LINKED")
    print("   1. Open the Facebook Filter page and click START SCRAPER")
    print("   2. Import your session with Cookie-Editor (or log in via the Chrome window)")
    print("   3. Click Start Auto-Scraper")
print("=" * 70)
