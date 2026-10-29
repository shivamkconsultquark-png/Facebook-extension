"""All SQL for the Facebook service. Raw SQL via SQLAlchemy text(), as in the Twitter backend."""
from __future__ import annotations

import json
import re

from sqlalchemy import text
from sqlalchemy.exc import OperationalError

from .config import settings

# The Twitter page's filter keys (clean/suspicious/spam) map onto the Facebook categories.
CATEGORY_ALIASES = {
    "clean": "likely_legitimate", "legitimate": "likely_legitimate", "likely_legitimate": "likely_legitimate",
    "suspicious": "suspicious",
    "spam": "likely_scam", "scam": "likely_scam", "likely_scam": "likely_scam",
    "unverified": "unable_to_verify", "unable_to_verify": "unable_to_verify",
}

SORTS = {
    "newest": "a.analyzed_at DESC, a.id DESC",
    "oldest": "a.analyzed_at ASC, a.id ASC",
    "score_desc": "a.score DESC, a.analyzed_at DESC",
    "score_asc": "a.score ASC, a.analyzed_at DESC",
}


# ---------- settings
def get_setting(conn, user_id: int, key: str) -> str | None:
    row = conn.execute(text("SELECT setting_value FROM user_settings WHERE user_id = :u AND setting_key = :k"),
                       {"u": user_id, "k": key}).fetchone()
    return row[0] if row else None


def set_setting(conn, user_id: int, key: str, value: str | None) -> None:
    if value is None:
        conn.execute(text("DELETE FROM user_settings WHERE user_id = :u AND setting_key = :k"), {"u": user_id, "k": key})
        return
    conn.execute(text(
        "INSERT INTO user_settings (user_id, setting_key, setting_value) VALUES (:u, :k, :v) "
        "ON DUPLICATE KEY UPDATE setting_value = VALUES(setting_value)"
    ), {"u": user_id, "k": key, "v": value})


# ---------- analysis cache (content-addressed, no post text)
def cache_get(conn, content_hash: str, engine_version: str) -> dict | None:
    row = conn.execute(text(
        "SELECT result_json FROM analysis_cache WHERE content_hash = :h AND engine_version = :v "
        "AND created_at > UTC_TIMESTAMP() - INTERVAL :d DAY"
    ), {"h": content_hash, "v": engine_version, "d": settings.retention_days}).fetchone()
    if not row:
        return None
    return json.loads(row[0]) if isinstance(row[0], str) else row[0]


def cache_put(conn, content_hash: str, engine_version: str, result: dict) -> None:
    conn.execute(text(
        "INSERT INTO analysis_cache (content_hash, engine_version, score, result_json) VALUES (:h, :v, :s, :j) "
        "ON DUPLICATE KEY UPDATE score = VALUES(score), result_json = VALUES(result_json), created_at = UTC_TIMESTAMP()"
    ), {"h": content_hash, "v": engine_version, "s": result["score"] or 0, "j": json.dumps(result)})


def ensure_model_version(conn, version: str) -> None:
    conn.execute(text("INSERT IGNORE INTO model_versions (version, description) VALUES (:v, :d)"),
                 {"v": version, "d": "auto-registered on first use"})


# ---------- post analyses
def upsert_analysis(conn, user_id: int, post, post_key: str, content_hash: str, result: dict, category: str,
                    extra: dict | None = None) -> bool:
    """Insert or refresh one scraped post. Returns True if the post is new for this user."""
    extra = extra or {}
    excerpt = post.text[: settings.post_text_max_chars] if settings.store_post_text else None
    # Explicit check: rowcount is unreliable here (SQLAlchemy enables CLIENT_FOUND_ROWS for MySQL).
    exists = conn.execute(text("SELECT 1 FROM fb_post_analyses WHERE user_id = :u AND post_key = :k"),
                          {"u": user_id, "k": post_key}).first() is not None
    conn.execute(text("""
        INSERT INTO fb_post_analyses
            (user_id, post_key, permalink, author_name, author_url, author_image, text_excerpt, media_url,
             media_urls, media_more, media_type, video_url, album_complete,
             content_hash, link_domains, reactions_count, comments_count, shares_count,
             score, category, reason, indicators, scoring_method, engine_version)
        VALUES (:u, :k, :pl, :an, :au, :ai, :tx, :mu, :mus, :mm, :mt, :vu, :ac, :h, :ld, :rc, :cc, :sc, :s, :c, :r, :ind, :m, :v)
        ON DUPLICATE KEY UPDATE
            permalink = COALESCE(VALUES(permalink), permalink),
            author_name = VALUES(author_name), author_url = COALESCE(VALUES(author_url), author_url),
            author_image = COALESCE(VALUES(author_image), author_image),
            text_excerpt = VALUES(text_excerpt),
            -- A later feed sighting only has 5 tiles: never replace a longer (complete) album with it.
            -- MySQL applies these left to right, so the album columns are compared before media_urls changes.
            album_complete = IF(album_complete = 1 AND VALUES(album_complete) = 0, 1, VALUES(album_complete)),
            media_more = IF(media_urls IS NULL OR JSON_LENGTH(COALESCE(VALUES(media_urls), '[]')) >= JSON_LENGTH(media_urls),
                            COALESCE(VALUES(media_more), media_more), media_more),
            media_url = IF(media_urls IS NULL OR JSON_LENGTH(COALESCE(VALUES(media_urls), '[]')) >= JSON_LENGTH(media_urls),
                           COALESCE(VALUES(media_url), media_url), media_url),
            media_urls = IF(media_urls IS NULL OR JSON_LENGTH(COALESCE(VALUES(media_urls), '[]')) >= JSON_LENGTH(media_urls),
                            COALESCE(VALUES(media_urls), media_urls), media_urls),
            media_type = COALESCE(VALUES(media_type), media_type),
            video_url = COALESCE(VALUES(video_url), video_url),
            content_hash = VALUES(content_hash), link_domains = VALUES(link_domains),
            reactions_count = COALESCE(VALUES(reactions_count), reactions_count),
            comments_count = COALESCE(VALUES(comments_count), comments_count),
            shares_count = COALESCE(VALUES(shares_count), shares_count),
            score = VALUES(score), category = VALUES(category),
            reason = VALUES(reason), indicators = VALUES(indicators),
            scoring_method = VALUES(scoring_method), engine_version = VALUES(engine_version),
            analyzed_at = UTC_TIMESTAMP()
    """), {
        "u": user_id, "k": post_key, "pl": post.permalink, "an": post.author_name, "au": post.author_url,
        "ai": _https(extra.get("author_image")), "tx": excerpt, "mu": _https(extra.get("media_url")),
        "mus": _media_list(extra.get("media_urls")), "mm": _media_more(extra.get("media_more")),
        "mt": extra.get("media_type") if extra.get("media_type") in MEDIA_TYPES else None,
        "vu": _fb_url(extra.get("video_url")), "ac": 1 if extra.get("album_complete") else 0,
        "h": content_hash, "ld": json.dumps(result["link_domains"]),
        "rc": _count(extra.get("reactions_count")), "cc": _count(extra.get("comments_count")),
        "sc": _count(extra.get("shares_count")),
        "s": result["score"] or 0, "c": category, "r": (result["reason"] or "")[:255],
        "ind": json.dumps(result["indicators"][:12]), "m": result["scoring_method"], "v": result["engine_version"],
    })
    return not exists


def _https(url) -> str | None:
    """Only keep https image URLs (Facebook CDN); anything else is dropped."""
    return url[:1024] if isinstance(url, str) and url.startswith("https://") else None


MEDIA_TYPES = ("photo", "video", "reel")
MAX_MEDIA = 30  # full albums are fetched from the photo viewer
# Dashboard "Type" filter -> media_type values.
MEDIA_FILTERS = {"posts": ("photo", None), "videos": ("video", "reel"), "reels": ("reel",)}


def _media_list(urls) -> str | None:
    if not isinstance(urls, list):
        return None
    keep = [u for u in (_https(x) for x in urls) if u]
    return json.dumps(list(dict.fromkeys(keep))[:MAX_MEDIA]) if keep else None


def _media_more(v) -> int | None:
    return v if isinstance(v, int) and 0 <= v < 1000 else None


def _fb_url(url) -> str | None:
    """Facebook video page URL only (it is handed to Facebook's own embedded player)."""
    return url[:512] if isinstance(url, str) and url.startswith("https://www.facebook.com/") else None


def video_url(conn, user_id: int, post_id: int) -> str | None:
    """The stored video page of one of the user's posts."""
    url = conn.execute(text("SELECT video_url FROM fb_post_analyses WHERE id = :id AND user_id = :u"),
                       {"id": post_id, "u": user_id}).scalar()
    return _fb_url(url)


def complete_album_ids(conn, user_id: int) -> set:
    """Post ids whose every photo is already stored, so the scraper doesn't reopen the album."""
    rows = conn.execute(text("SELECT post_key FROM fb_post_analyses WHERE user_id = :u AND album_complete = 1"),
                        {"u": user_id})
    return {r[0][3:] for r in rows if r[0].startswith("fb:")}


def _count(v) -> str | None:
    return v[:16] if isinstance(v, str) and re.fullmatch(r"\d[\d.,]*[KkMm]?", v) else None


def _feed_filters(user_id: int, category=None, search=None, date_from=None, date_to=None, hide_above=None,
                  media=None):
    """WHERE clause over fb_post_analyses aliased as `a`."""
    where, params = ["a.user_id = :u"], {"u": user_id}
    if media in MEDIA_FILTERS:
        types = [t for t in MEDIA_FILTERS[media] if t]
        params.update({f"mt{i}": t for i, t in enumerate(types)})
        cond = f"a.media_type IN ({', '.join(f':mt{i}' for i in range(len(types)))})"
        if None in MEDIA_FILTERS[media]:
            cond = f"({cond} OR a.media_type IS NULL)"
        where.append(cond)
    if category:
        where.append("a.category = :cat")
        params["cat"] = CATEGORY_ALIASES.get(category, category)
    if search:
        where.append("(a.text_excerpt LIKE :q OR a.author_name LIKE :q OR a.reason LIKE :q)")
        params["q"] = "%" + search.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
    if date_from:
        where.append("a.analyzed_at >= :df")
        params["df"] = f"{date_from} 00:00:00"
    if date_to:
        where.append("a.analyzed_at < :dt + INTERVAL 1 DAY")
        params["dt"] = f"{date_to} 00:00:00"
    if hide_above is not None:
        where.append("a.score < :ha")
        params["ha"] = hide_above
    return " AND ".join(where), params


def feed(conn, user_id: int, page: int, limit: int, sort: str, **filters) -> dict:
    """Same envelope as /twitter/feed: {feed, total, total_pages, page}."""
    where, params = _feed_filters(user_id, **filters)
    total = conn.execute(text(f"SELECT COUNT(*) FROM fb_post_analyses a WHERE {where}"), params).scalar() or 0
    rows = conn.execute(text(f"""
        SELECT a.id, a.post_key, a.permalink, a.author_name, a.author_url, a.author_image, a.text_excerpt,
               a.media_url, a.link_domains, a.reactions_count, a.comments_count, a.shares_count,
               a.score, a.category, a.reason, a.indicators, a.scoring_method, a.first_seen_at, a.analyzed_at,
               a.media_urls, a.media_more, a.media_type, a.video_url
        FROM fb_post_analyses a
        WHERE {where}
        ORDER BY {SORTS.get(sort, SORTS['newest'])}
        LIMIT :lim OFFSET :off
    """), {**params, "lim": limit, "off": (page - 1) * limit}).fetchall()

    def _j(v):
        return json.loads(v) if isinstance(v, str) else (v or [])

    items = [{
        "id": r[0], "post_key": r[1], "permalink": r[2],
        "user": {"name": r[3], "url": r[4], "profile_image_url": r[5]},
        "text": r[6], "media_url": r[7], "link_domains": _j(r[8]),
        # Older rows only have media_url; expose it as a one-item gallery.
        "media_urls": _j(r[19]) or ([r[7]] if r[7] else []), "media_more": r[20] or 0, "media_type": r[21],
        "video_url": r[22],
        "reactions_count": r[9], "comments_count": r[10], "shares_count": r[11],
        "spam_score": None if r[13] == "unable_to_verify" else r[12],
        "category": r[13], "spam_reason": r[14], "indicators": _j(r[15]), "scoring_method": r[16],
        "created_at": r[17].isoformat() + "Z", "analyzed_at": r[18].isoformat() + "Z",
    } for r in rows]
    return {"feed": items, "total": total, "page": page,
            "total_pages": max(1, -(-total // limit))}


def stats(conn, user_id: int) -> dict:
    """Same keys as /twitter/config (total/unique/spam_* buckets) so the page renders identically."""
    row = conn.execute(text("""
        SELECT COUNT(*), COUNT(DISTINCT author_name),
               SUM(category = 'likely_legitimate'), SUM(category = 'suspicious'),
               SUM(category = 'likely_scam'), SUM(category = 'unable_to_verify'), MAX(analyzed_at),
               SUM(media_type IN ('video', 'reel'))
        FROM fb_post_analyses WHERE user_id = :u
    """), {"u": user_id}).fetchone()
    return {
        "total_posts": int(row[0] or 0), "unique_authors": int(row[1] or 0),
        "spam_clean": int(row[2] or 0), "spam_suspicious": int(row[3] or 0),
        "spam_spam": int(row[4] or 0), "unverified": int(row[5] or 0),
        "total_videos": int(row[7] or 0),
        "last_synced_at": row[6].strftime("%Y-%m-%d %H:%M UTC") if row[6] else None,
        "retention_days": settings.retention_days,
    }


def _purges() -> dict:
    """name -> (DELETE statement, params) for every table with a retention limit."""
    d = settings.retention_days
    return {
        "analyses": ("DELETE FROM fb_post_analyses WHERE analyzed_at < UTC_TIMESTAMP() - INTERVAL :d DAY", {"d": d}),
        "cache": ("DELETE FROM analysis_cache WHERE created_at < UTC_TIMESTAMP() - INTERVAL :d DAY", {"d": d}),
        "feedback": ("DELETE FROM fb_feedback WHERE created_at < UTC_TIMESTAMP() - INTERVAL :d DAY",
                     {"d": settings.feedback_retention_days}),
        "tokens": ("DELETE FROM api_tokens WHERE expires_at < UTC_TIMESTAMP()", {}),
        "audit": ("DELETE FROM audit_events WHERE created_at < UTC_TIMESTAMP() - INTERVAL :d DAY",
                  {"d": settings.audit_retention_days}),
    }


def purge_expired(conn) -> dict:
    """Every purge in the caller's transaction."""
    return {name: conn.execute(text(sql), params).rowcount for name, (sql, params) in _purges().items()}


def purge_expired_separately(connect) -> dict:
    """Each purge in its own short transaction, retried once on a deadlock, so the
    background purge holds few locks and never makes a user's request fail."""
    out = {}
    for name, (sql, params) in _purges().items():
        for attempt in (1, 2):
            try:
                with connect() as conn:
                    out[name] = conn.execute(text(sql), params).rowcount
                break
            except OperationalError as e:
                if attempt == 2 or getattr(e.orig, "args", [None])[0] != 1213:
                    raise
    return out
