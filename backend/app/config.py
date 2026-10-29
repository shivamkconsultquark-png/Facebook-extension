"""Environment-driven settings. Nothing deployment-specific is hardcoded elsewhere."""
import os
import pathlib
import urllib.parse

from dotenv import load_dotenv

BACKEND_DIR = pathlib.Path(__file__).resolve().parents[1]
# backend/.env by absolute path: services and scheduled tasks don't start in this folder.
# Variables already set in the environment win over the file.
load_dotenv(BACKEND_DIR / ".env")

APP_VERSION = "1.1.0"


def _bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in ("1", "true", "yes", "on")


def _int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, default))
    except (TypeError, ValueError):
        return default


def database_url() -> str:
    """Same resolution order as daily_twitter_sync.py: DB_* parts first, then DATABASE_URL."""
    user, name = os.getenv("DB_USER"), os.getenv("DB_NAME")
    if user and name:
        pw = urllib.parse.quote_plus(os.getenv("DB_PASS", ""))
        host = os.getenv("DB_HOST", "localhost")
        port = os.getenv("DB_PORT", "3306")
        return f"mysql+pymysql://{user}:{pw}@{host}:{port}/{name}?charset=utf8mb4"
    url = os.getenv("DATABASE_URL", "")
    for driver in ("mysql+aiomysql", "mysql+mysqldb"):
        url = url.replace(driver, "mysql+pymysql")
    return url


class Settings:
    database_url = database_url()

    # LLM (Gemini, as in the Twitter backend). Empty key => rules-only scoring.
    gemini_api_key = os.getenv("GEMINI_API_KEY", "")
    # flash-lite answers in ~2 s; the full flash models frequently time out (504) or are overloaded (503).
    gemini_model = os.getenv("GEMINI_MODEL", "gemini-flash-lite-latest")
    gemini_fallback_models = [m.strip() for m in os.getenv(
        "GEMINI_FALLBACK_MODELS", "gemini-3.1-flash-lite,gemini-flash-latest").split(",") if m.strip()]
    llm_timeout_ms = _int("LLM_TIMEOUT_MS", 10_000)
    llm_workers = _int("LLM_WORKERS", 4)  # posts scored in parallel per scrape

    # Optional Google Safe Browsing v4 lookup (a reputation API; we never fetch post URLs).
    safe_browsing_api_key = os.getenv("SAFE_BROWSING_API_KEY", "")

    # Privacy / retention
    store_post_text = _bool("STORE_POST_TEXT", True)
    post_text_max_chars = _int("POST_TEXT_MAX_CHARS", 1000)
    retention_days = _int("RETENTION_DAYS", 30)
    feedback_retention_days = _int("FEEDBACK_RETENTION_DAYS", 180)

    # Abuse controls
    max_posts_per_request = _int("MAX_POSTS_PER_REQUEST", 20)
    max_text_chars = _int("MAX_TEXT_CHARS", 5000)
    rate_limit_posts_per_minute = _int("RATE_LIMIT_POSTS_PER_MINUTE", 600)
    allow_registration = _bool("ALLOW_REGISTRATION", True)
    token_ttl_days = _int("TOKEN_TTL_DAYS", 30)

    # CORS: dashboard origins + chrome-extension://<id> origins, comma separated
    cors_origins = [o.strip() for o in os.getenv("CORS_ORIGINS", "").split(",") if o.strip()]

    # Server-side Facebook browser scraper (same model as the Twitter auto-scraper)
    session_encryption_key = os.getenv("SESSION_ENCRYPTION_KEY", "")
    scraper_headless = _bool("SCRAPER_HEADLESS", True)
    scraper_browser_channel = os.getenv("SCRAPER_BROWSER_CHANNEL", "chrome")  # "chrome", "msedge" or "" (bundled Chromium)
    scraper_scrolls = _int("SCRAPER_SCROLLS", 25)
    scraper_max_posts = _int("SCRAPER_MAX_POSTS", 60)
    scraper_reels = _int("SCRAPER_REELS", 10)  # reels checked from the Reels tab per cycle (0 = feed only)
    scraper_max_albums = _int("SCRAPER_MAX_ALBUMS", 5)  # "+N" albums opened per cycle to get every photo
    scraper_album_max = _int("SCRAPER_ALBUM_MAX", 30)  # photos kept per album
    scraper_resume = _bool("SCRAPER_RESUME", True)  # restart running scrapers when the backend starts
    scraper_default_interval = _int("SCRAPER_DEFAULT_INTERVAL", 300)
    scraper_max_browsers = _int("SCRAPER_MAX_BROWSERS", 2)
    # A visible Chrome window for "log in to Facebook" only works where the server has a desktop.
    scraper_allow_login_window = _bool("SCRAPER_ALLOW_LOGIN_WINDOW", True)
    scraper_login_timeout = _int("SCRAPER_LOGIN_TIMEOUT", 300)

    # Logs: rotated files in LOG_DIR (relative paths are under backend/); empty = console only.
    log_dir = os.getenv("LOG_DIR", "logs")
    log_level = os.getenv("LOG_LEVEL", "INFO").upper()
    audit_retention_days = _int("AUDIT_RETENTION_DAYS", 365)


settings = Settings()


def config_problems(s: Settings = settings) -> list[str]:
    """Settings that would make the service fail or misbehave; logged at startup and shown by /health."""
    problems = []
    if not s.database_url:
        problems.append("database not configured (DB_USER/DB_NAME or DATABASE_URL)")
    if s.retention_days < 1:
        problems.append("RETENTION_DAYS must be at least 1")
    if not 1 <= s.scraper_max_browsers <= 8:
        problems.append("SCRAPER_MAX_BROWSERS must be between 1 and 8")
    if s.log_level not in ("DEBUG", "INFO", "WARNING", "ERROR"):
        problems.append("LOG_LEVEL must be DEBUG, INFO, WARNING or ERROR")
    return problems
