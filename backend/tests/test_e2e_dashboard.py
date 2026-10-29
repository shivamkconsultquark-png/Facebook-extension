"""End to end: built dashboard -> real backend (uvicorn) -> MySQL, in real Chrome.

Needs TEST_DATABASE_URL (a throwaway database) and E2E=1, because it builds the dashboard
and starts its own backend and dashboard server on free ports:

    $env:TEST_DATABASE_URL="mysql+pymysql://user:pass@127.0.0.1:3307/fbs_test"; $env:E2E="1"
    ..\\.venv\\Scripts\\python -m pytest -q tests/test_e2e_dashboard.py
"""
import json
import os
import pathlib
import shutil
import socket
import subprocess
import sys
import time
import urllib.request
import uuid

import pytest
from sqlalchemy import text

ROOT = pathlib.Path(__file__).resolve().parents[2]
pytestmark = pytest.mark.skipif(os.getenv("E2E") != "1", reason="set E2E=1 to run the browser end-to-end test")


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _wait_http(url, timeout=60) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=3):
                return True
        except Exception:
            time.sleep(0.5)
    return False


class Backend:
    def __init__(self, port, dash_port):
        self.port, self.proc = port, None
        self.env = dict(os.environ, DB_USER="", DB_NAME="", DATABASE_URL=os.environ["TEST_DATABASE_URL"],
                        CORS_ORIGINS=f"http://localhost:{dash_port}", LOG_DIR="", SCRAPER_RESUME="false",
                        GEMINI_API_KEY="", PYTHONUNBUFFERED="1")

    def start(self):
        self.proc = subprocess.Popen([sys.executable, "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1",
                                      "--port", str(self.port)], cwd=ROOT / "backend", env=self.env,
                                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        assert _wait_http(f"http://127.0.0.1:{self.port}/health/live"), "backend did not start"

    def kill(self):
        self.proc.kill()
        self.proc.wait(10)


@pytest.fixture(scope="module")
def stack(mysql_engine, tmp_path_factory):
    api_port, dash_port = _free_port(), _free_port()
    out = tmp_path_factory.mktemp("dist")
    npm = shutil.which("npm") or shutil.which("npm.cmd")
    subprocess.run([npm, "run", "build", "--", "--outDir", str(out), "--emptyOutDir"], cwd=ROOT / "dashboard",
                   env=dict(os.environ, VITE_FB_SHIELD_API=f"http://localhost:{api_port}"),
                   check=True, capture_output=True, shell=os.name == "nt")
    site = tmp_path_factory.mktemp("site")
    (site / "dashboard").mkdir()
    shutil.copytree(out, site / "dashboard" / "dist")
    dash = subprocess.Popen([sys.executable, str(ROOT / "ops" / "serve_dashboard.py"), "--port", str(dash_port),
                             "--root", str(site)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    backend = Backend(api_port, dash_port)
    backend.start()
    assert _wait_http(f"http://127.0.0.1:{dash_port}/facebook")
    yield {"api": f"http://127.0.0.1:{api_port}", "dash": f"http://localhost:{dash_port}", "backend": backend}
    if backend.proc.poll() is None:
        backend.kill()
    dash.kill()


def _register(api) -> tuple[str, int]:
    body = json.dumps({"email": f"e2e{uuid.uuid4().hex[:8]}@example.com", "password": "e2e-pass-123"}).encode()
    req = urllib.request.Request(f"{api}/auth/register", data=body, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req) as r:
        d = json.load(r)
    return d["token"], d["user"]["id"]


def _seed(engine, user_id):
    rows = [("fb:e2e1", "photo", "Win a free iPhone, pay the delivery fee by gift card", 88, "likely_scam"),
            ("fb:e2e2", "reel", "Our new bakery opens on Saturday", 5, "likely_legitimate"),
            ("fb:e2e3", None, "Family picnic at the lake", 3, "likely_legitimate")]
    with engine.begin() as conn:
        for key, media, body, score, cat in rows:
            conn.execute(text(
                "INSERT INTO fb_post_analyses (user_id, post_key, content_hash, author_name, text_excerpt, media_type, "
                "score, category, reason, scoring_method, engine_version) VALUES "
                "(:u, :k, :h, 'E2E Page', :t, :m, :s, :c, 'test', 'rules', 'test')"),
                {"u": user_id, "k": key, "h": key.ljust(64, "0")[:64], "t": body, "m": media, "s": score, "c": cat})


def test_dashboard_workflow_and_backend_outage(stack, mysql_engine):
    from playwright.sync_api import sync_playwright

    token, user_id = _register(stack["api"])
    _seed(mysql_engine, user_id)
    with sync_playwright() as p:
        browser = p.chromium.launch(channel="chrome", headless=True)
        page = browser.new_page()
        page.goto(f"{stack['dash']}/facebook")
        page.evaluate("t => localStorage.setItem('fbshield_session', JSON.stringify({token: t, user: {id: 0}}))", token)

        # 1. Loads the stored posts; no date filter by default.
        page.goto(f"{stack['dash']}/facebook")
        page.get_by_text("Family picnic at the lake").wait_for(timeout=15000)
        assert page.locator('[aria-label="Date Range"]').inner_text().strip() == "Select Range"
        assert page.get_by_text("Win a free iPhone").count() == 1

        # 2. Type filter goes through the API and narrows the timeline.
        page.locator("select").filter(has_text="All types").select_option(index=2)  # Videos & reels
        page.get_by_text("Family picnic at the lake").wait_for(state="detached", timeout=10000)
        assert page.get_by_text("Our new bakery opens").count() == 1
        page.locator("select").filter(has_text="All types").select_option(index=0)
        page.get_by_text("Family picnic at the lake").wait_for(timeout=10000)

        # 3. Direct navigation to a client-side route works (SPA fallback).
        page.goto(f"{stack['dash']}/facebook/auto-scraper")
        page.get_by_text("Start Auto-Scraper").first.wait_for(timeout=15000)

        # 4. Backend goes down: a clear error, and the posts already shown stay.
        page.goto(f"{stack['dash']}/facebook")
        page.get_by_text("Family picnic at the lake").wait_for(timeout=15000)
        stack["backend"].kill()
        page.get_by_role("button", name="Refresh Data").click()
        page.get_by_text("Can't reach the Facebook Filter server").wait_for(timeout=15000)
        assert page.get_by_text("Family picnic at the lake").count() == 1

        # 5. Backend returns: the dashboard recovers by itself (automatic retry, no reload).
        stack["backend"].start()
        page.get_by_text("Can't reach the Facebook Filter server").wait_for(state="detached", timeout=30000)
        assert page.get_by_text("Family picnic at the lake").count() == 1
        browser.close()
