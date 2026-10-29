"""Score and store Facebook posts (used by the browser scraper).

Same flow the Twitter scraper uses per tweet: check the score cache, otherwise run the
engine (Gemini + rules, with this user's custom LLM instructions), then upsert the row.
Posts are scored in parallel (LLM_WORKERS) because each Gemini call takes a second or two.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor

from . import db, repository as repo
from .config import settings
from .detection import engine, llm
from .platforms.facebook.schemas import FacebookPost


def score_post(post: FacebookPost, instructions: str | None) -> tuple[str, dict]:
    item = post.to_content()
    h = engine.content_hash(item, instructions)
    version = engine.engine_version()
    with db.connect() as conn:
        cached = repo.cache_get(conn, h, version)
    if cached is not None:
        return h, cached
    result = engine.analyze(item, instructions).to_dict()
    # A rules-only result while Gemini is configured means Gemini failed this time:
    # don't cache it under the Gemini version, so the next scrape retries with Gemini.
    fell_back = llm.available() and result.get("scoring_method") == "rules"
    if result["status"] == "ok" and not fell_back:
        with db.connect() as conn:
            repo.cache_put(conn, h, version, result)
    return h, result


def store_posts(user_id: int, scraped: list[dict]) -> dict:
    """Score + upsert scraped posts. Returns {"stored", "new", "skipped", "videos"}."""
    with db.connect() as conn:
        instructions = repo.get_setting(conn, user_id, "llm_instructions")
    parsed, skipped = [], 0
    for item in scraped:
        try:
            parsed.append((item, FacebookPost(client_id="scraper", **item["post"])))
        except Exception:
            skipped += 1
    with ThreadPoolExecutor(max_workers=max(1, settings.llm_workers)) as pool:
        scores = list(pool.map(lambda p: score_post(p[1], instructions), parsed))
    stored = new = videos = 0
    for (item, post), (h, result) in zip(parsed, scores):
        if result["status"] != "ok":
            skipped += 1
            continue
        category = engine.category_for(result["score"])
        with db.connect() as conn:
            repo.ensure_model_version(conn, result["engine_version"])
            new += repo.upsert_analysis(conn, user_id, post, post.post_key(), h, result, category, extra=item)
        stored += 1
        videos += item.get("media_type") in ("video", "reel")
    return {"stored": stored, "new": new, "skipped": skipped, "videos": videos}
