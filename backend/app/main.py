"""FastAPI app for the Facebook Scam Shield backend.

Run:  uvicorn app.main:app --host 0.0.0.0 --port 8000
"""
import logging
import logging.handlers
import os
import shutil
import threading
import time
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from . import db, repository
from .config import APP_VERSION, BACKEND_DIR, config_problems, settings
from .detection import engine, llm, reputation
from .platforms.facebook.scraper import manager as scraper_manager
from .routers import auth_routes, facebook

LOG_FORMAT = "%(asctime)s %(levelname)s %(name)s: %(message)s"
MIN_FREE_DISK_MB = 1024


def _setup_logging() -> str | None:
    """Console plus, unless LOG_DIR is empty, backend/logs/backend.log rotated at 10 MB x 10 files."""
    root = logging.getLogger()
    root.setLevel(getattr(logging, settings.log_level, logging.INFO))
    if not any(isinstance(h, logging.StreamHandler) and not isinstance(h, logging.FileHandler) for h in root.handlers):
        h = logging.StreamHandler()
        h.setFormatter(logging.Formatter(LOG_FORMAT))
        root.addHandler(h)
    if not settings.log_dir:
        return None
    log_dir = BACKEND_DIR / settings.log_dir
    path = str(log_dir / "backend.log")
    if any(getattr(h, "baseFilename", None) == path for h in root.handlers):
        return path
    try:
        log_dir.mkdir(parents=True, exist_ok=True)
        fh = logging.handlers.RotatingFileHandler(path, maxBytes=10 * 1024 * 1024, backupCount=10, encoding="utf-8")
    except OSError as e:  # unwritable log folder: keep running with console logs
        logging.getLogger("fbshield").warning("file logging disabled: %s", e)
        return None
    fh.setFormatter(logging.Formatter(LOG_FORMAT))
    root.addHandler(fh)
    return path


_log_file = _setup_logging()
log = logging.getLogger("fbshield")

PURGE_INTERVAL_SECONDS = 6 * 3600


def _purge_loop(stop: threading.Event):
    while not stop.is_set():
        try:
            removed = repository.purge_expired_separately(db.connect)
            log.info("retention purge: %s", removed)
        except Exception as e:
            log.warning("retention purge failed: %s", e)
        stop.wait(PURGE_INTERVAL_SECONDS)


@asynccontextmanager
async def lifespan(app: FastAPI):
    log.info("starting Facebook Scam Shield %s (pid %s, log file %s)", APP_VERSION, os.getpid(), _log_file or "none")
    for problem in config_problems():
        log.error("configuration problem: %s", problem)
    log.info("engine %s | llm=%s | safe_browsing=%s", engine.engine_version(), llm.available(), reputation.available())
    pending = db.pending_migrations()
    if pending:
        log.error("database migrations not applied: %s (run migrate.py)", ", ".join(pending))
    stop = threading.Event()
    threading.Thread(target=_purge_loop, args=(stop,), daemon=True, name="retention").start()
    # Scrapers that were running before a restart carry on, once MySQL is reachable.
    scraper_manager.resume_in_background(stop)
    yield
    log.info("shutting down: stopping scrapers")
    scraper_manager.shutdown()
    stop.set()
    log.info("stopped")


app = FastAPI(title="Facebook Scam Shield API", version=APP_VERSION, lifespan=lifespan)

# Extension origins are chrome-extension://<id>; list them (and dashboard origins) in CORS_ORIGINS.
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=False,
    allow_methods=["GET", "POST", "PUT"],
    allow_headers=["Authorization", "Content-Type", "X-Requested-With"],
    max_age=600,
)


@app.middleware("http")
async def body_size_limit(request: Request, call_next):
    if int(request.headers.get("content-length") or 0) > 512 * 1024:
        return JSONResponse({"detail": "Request body too large"}, status_code=413)
    return await call_next(request)


@app.exception_handler(RequestValidationError)
async def validation_error(_: Request, exc: RequestValidationError):
    # Don't echo submitted post text back in error payloads.
    errors = [{"loc": e.get("loc"), "msg": e.get("msg")} for e in exc.errors()]
    return JSONResponse({"detail": "Invalid request", "errors": errors}, status_code=422)


@app.get("/health/live")
def live():
    """Liveness: the process answers requests. Doesn't touch the database."""
    return {"status": "ok", "version": APP_VERSION}


@app.get("/health")
def health():
    """Readiness: 503 unless the database answers, migrations are applied and the configuration is valid."""
    ok = db.ping()
    pending = db.pending_migrations() if ok else None
    problems = config_problems()
    scrapers = scraper_manager.health()
    free_mb = shutil.disk_usage(BACKEND_DIR).free // (1024 * 1024)
    ready = ok and pending == [] and not problems
    warnings = []
    if scrapers["dead_workers"]:
        warnings.append("scraper thread stopped unexpectedly")
    if free_mb < MIN_FREE_DISK_MB:
        warnings.append(f"low disk space: {free_mb} MB free")
    return JSONResponse({
        "status": "ok" if ready else "degraded",
        "version": APP_VERSION,
        "database": ok,
        "migrations_pending": pending,
        "config_problems": problems,
        "scrapers": scrapers,
        "disk_free_mb": free_mb,
        "warnings": warnings,
        "llm_enabled": llm.available(),
        "safe_browsing_enabled": reputation.available(),
        "engine_version": engine.engine_version(),
        "time": int(time.time()),
    }, status_code=200 if ready else 503)


app.include_router(auth_routes.router)
app.include_router(facebook.router)
