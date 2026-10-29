#!/usr/bin/env python3
"""Re-score stored analyses with the current engine (e.g. after a ruleset update).

Uses the stored text excerpt, so posts saved with STORE_POST_TEXT=false are skipped.
Usage:  python rescore.py            # all users
"""
import json
import pathlib
import sys

from sqlalchemy import text

sys.path.insert(0, str(pathlib.Path(__file__).parent))
from app import db, repository as repo  # noqa: E402
from app.detection import engine  # noqa: E402
from app.platforms.facebook.schemas import clean_post_text  # noqa: E402


def main():
    changed = total = 0
    with db.connect() as conn:
        rows = conn.execute(text(
            "SELECT id, user_id, author_name, text_excerpt, link_domains, category FROM fb_post_analyses "
            "WHERE text_excerpt IS NOT NULL")).fetchall()
        instr_cache = {}
        for row_id, user_id, author, excerpt, domains, old_cat in rows:
            if user_id not in instr_cache:
                instr_cache[user_id] = repo.get_setting(conn, user_id, "llm_instructions")
            urls = ["https://" + d for d in (json.loads(domains) if isinstance(domains, str) else domains or [])]
            r = engine.analyze(engine.ContentItem(excerpt, urls, author), instr_cache[user_id]).to_dict()
            if r["status"] != "ok":
                continue
            cat = engine.category_for(r["score"])
            # analyzed_at = analyzed_at keeps the original check time (the column auto-updates otherwise).
            conn.execute(text(
                "UPDATE fb_post_analyses SET score=:s, category=:c, reason=:r, indicators=:i, "
                "scoring_method=:m, engine_version=:v, text_excerpt=:t, analyzed_at=analyzed_at WHERE id=:id"),
                {"s": r["score"], "c": cat, "r": r["reason"][:255], "i": json.dumps(r["indicators"][:12]),
                 "m": r["scoring_method"], "v": r["engine_version"], "t": clean_post_text(excerpt), "id": row_id})
            total += 1
            changed += cat != old_cat
        conn.execute(text("DELETE FROM analysis_cache"))
    print(f"re-scored {total} posts, {changed} changed category; score cache cleared")


if __name__ == "__main__":
    main()
