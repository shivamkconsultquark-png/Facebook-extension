#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Manually run one Facebook scrape cycle now (for testing / immediate sync).
Facebook counterpart of the Twitter backend's run_sync_now.py.

Usage:  python run_scrape_now.py            # every user with a linked Facebook session
        python run_scrape_now.py <user_id>
"""
import logging
import sys
from datetime import datetime

sys.stdout.reconfigure(encoding='utf-8')
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("fbshield")

print("=" * 70)
print("MANUAL FACEBOOK SCRAPE RUN")
print(f"Started at: {datetime.now()}")
print("=" * 70)

try:
    from sqlalchemy import text
    from app import db, scoring
    from app.platforms.facebook import scraper, session as fb_session

    with db.connect() as conn:
        ids = [int(sys.argv[1])] if len(sys.argv) > 1 else [r[0] for r in conn.execute(text("SELECT user_id FROM fb_sessions"))]
    if not ids:
        print("\n❌ No linked Facebook sessions. Import one from the Facebook Auto-Scraper page first.")
        sys.exit(1)
    for user_id in ids:
        with db.connect() as conn:
            cookies, acct = fb_session.load(conn, user_id)
        if not cookies:
            print(f"user {user_id}: no usable session")
            continue
        res = scraper.browse_feed(cookies, want_name=not (acct and acct.get("name")))
        if not res["logged_in"]:
            print(f"user {user_id}: ❌ login required — {res.get('reason')}")
            continue
        with db.connect() as conn:
            fb_session.save(conn, user_id, res["cookies"], res.get("account_name"))
        stored = scoring.store_posts(user_id, res["posts"])
        print(f"user {user_id}: ✅ {stored['stored']} posts scored ({stored['new']} new, {stored['skipped']} skipped)")
    print("\n✅ SCRAPE COMPLETED")
except Exception as e:
    logger.error(f"Scrape failed: {e}", exc_info=True)
    print(f"\n❌ SCRAPE FAILED: {e}")
    sys.exit(1)

print(f"\nCompleted at: {datetime.now()}")
print("=" * 70)
