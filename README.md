# Facebook Filter + Facebook Auto-Scraper

The Facebook counterpart of the **X Filter** page and **X Auto-Scraper** on finance.opcozero.com. It has the same UI, buttons, endpoints and session model, retargeted to Facebook and to scam/fraud detection.

```
backend/                  FastAPI + MySQL
  app/platforms/facebook/
    scraper.py            server-side auto-scraper (Playwright driving the installed Chrome)
    session.py            Cookie-Editor import, c_user/xs validation, Fernet encryption
    js/                   in-page collector injected into the Facebook feed
  app/detection/          scoring engine: rules + URL analysis + optional Gemini / Safe Browsing
  app/routers/facebook.py /facebook/* endpoints (mirror /twitter/*)
  migrations/             versioned SQL (migrate.py)
  run_scrape_now.py       ← run_sync_now.py
  check_facebook_config.py← check_twitter_config.py
  rescore.py              re-score stored posts after a rules update
dashboard/src/pages/
  FacebookInsights.jsx    ← TwitterInsights.jsx   (route /facebook)
  FacebookScraper.jsx     ← AutoScraper.jsx       (modal + route /facebook/auto-scraper)
docs/                     API.md, DETECTION.md, PRIVACY.md
```

## 1. How the Twitter scraper works, and how each piece maps

This is based on the supplied `TwitterInsights.jsx` and `AutoScraper.jsx`, cross-checked against the live bundle at finance.opcozero.com: routes `/twitter` and `/twitter/auto-scraper`, sidebar item `{id: "x-filter", label: "X Filter", path: "/twitter"}`.

| Twitter element | What it does | Facebook version |
|---|---|---|
| Sidebar "X Filter" → `/twitter` | TwitterInsights page | "Facebook Filter" → `/facebook` |
| Header chip "Scraping / Scraper stopped" | polls `/twitter/browser-scraper/status` every 10 s | polls `/facebook/browser-scraper/status` |
| **STATS** | toggles the stats strip, Sync Details and Spam Analysis; remembered in `localStorage` | same (`fbfilter_show_stats`) |
| **START SCRAPER / SCRAPER RUNNING** | opens AutoScraper as an in-page modal | opens FacebookScraper as an in-page modal |
| **REFRESH DATA** | reloads feed + `/config` aggregates | same |
| Spam Analysis bars | click to filter `clean` / `suspicious` / `spam` | same keys (the backend maps them to Facebook categories) |
| Timeline filters | search, date range, Hide ≥ %, sort, Clear, pagination top and bottom | same |
| Score badge | hover shows Clean/Suspicious/Spam + reason | same |
| AutoScraper **Status** box | message, next-scrape countdown, last cycle count, total, cycles, last run (5 s poll) | same fields |
| **Switch X Account** | confirm → `POST switch-account` (stop + remove session) | **Switch Facebook Account** |
| **Scrape Interval** | 2 min … 24 h; locked while running | same 12 options (validated server-side) |
| **Start / Stop Auto-Scraper** | `POST start {interval_seconds}` / `POST stop`; Start disabled until an account is linked | same |
| "Login required" box | session expired; resumes after import | same, plus a **Log in to Facebook** button |
| **Spam Score LLM Instructions** | edit / save / restore the Gemini prompt | same endpoints under `/facebook` |
| **Link account by session import** | Cookie-Editor → Export as JSON → paste → **Import Session**; live check for `auth_token` | same flow; live check for Facebook's `c_user` + `xs` |
| "click Start and log in once" | backend opens a Chrome window for login | **Log in to Facebook (opens a Chrome window)** |
| `/twitter/connect` (Integrations) + `daily_twitter_sync.py` | tracked handles via X syndication | **not ported**: Facebook has no public equivalent without the Graph API |

**Reels, videos and photo albums.** Each cycle checks the home feed (photo posts, videos and reels) and then `SCRAPER_REELS` reels from the Reels tab, 10 by default, 0 to turn off. Reel captions are scored like post text. A reel with no caption and no link has nothing to score and is skipped. For multi-photo posts, the feed only renders 5 tiles. "+N" on the 5th tile means the album has 4 + N photos. So for each new "+N" post, up to `SCRAPER_MAX_ALBUMS` per cycle (5 by default), the scraper opens that post's photo viewer once. It presses "Next photo" until the viewer wraps around and stores every photo, up to `SCRAPER_ALBUM_MAX` (30). Complete albums are not reopened on later cycles. The timeline lays photos out like the Facebook feed (1, 2, 3, 2×2, or 2 + 3 with "+N"), and any tile opens a full-screen viewer with every stored photo. Videos and reels play inside the dashboard through Facebook's official embedded player (`plugins/video.php`, or the post embed when the feed exposes no video link). Videos whose owner has disabled embedding show a "watch on Facebook" link instead. Its **Type** filter switches between All, Posts & photos, Videos & reels, and Reels only.

**How sessions work.** Importing or logging in stores the Facebook cookies, encrypted, per dashboard user. Every interval, a Chrome window controlled by the backend (headless by default) opens `facebook.com` with those cookies. It scrolls the home feed `SCRAPER_SCROLLS` times, expands "See more", collects posts, and saves the refreshed cookies back, which keeps the session alive. If Facebook shows a login or checkpoint page, the status turns to "Login required" and the scraper waits for a new import. Running scrapers resume after a backend restart.

## 2. Setup

Requirements: Python 3.10+, MySQL 8, Google Chrome, Node 18+ (dashboard harness only).

```powershell
cd backend
..\.venv\Scripts\pip install -r requirements.txt
Copy-Item .env.example .env        # set DB_*, optionally GEMINI_API_KEY, SESSION_ENCRYPTION_KEY
..\.venv\Scripts\python migrate.py
..\.venv\Scripts\python check_facebook_config.py   # DB, browser launch, linked sessions
..\.venv\Scripts\python -m uvicorn app.main:app --host 127.0.0.1 --port 8001
```
No Chrome on the server? Set `SCRAPER_BROWSER_CHANNEL=` and run `python -m playwright install chromium`. On a headless Linux server, use session import (set `SCRAPER_ALLOW_LOGIN_WINDOW=false`), because the login window needs a desktop.

Dashboard harness: `cd dashboard`, set `VITE_FB_SHIELD_API=http://localhost:8001` in `.env`, run `npm install`, then `npm run dev`. Open http://localhost:5173/facebook.

**Use it:**
1. Click **START SCRAPER**.
2. Link your account: either **Import Session** (Cookie-Editor export from facebook.com) or **Log in to Facebook**.
3. Pick an interval and click **Start Auto-Scraper**.
4. Posts appear on the timeline. Click **REFRESH DATA** to see new ones.

## 3. Adding it to finance.opcozero.com

1. **Frontend:** copy `dashboard/src/pages/FacebookInsights.jsx` and `FacebookScraper.jsx` next to `TwitterInsights.jsx`. They import the same modules (`useAuth`, `API_MAIN`, `AdvancedDatePicker`, `Skeleton`, `toZonedDate`/`toZonedTime`, `DashboardAnalytics.css`). Then add:
   ```jsx
   <Route path="/facebook" element={<FacebookInsights />} />
   <Route path="/facebook/auto-scraper" element={<FacebookScraper />} />
   // sidebar, next to X Filter:
   { id: "facebook-filter", label: "Facebook Filter", icon: Facebook, path: "/facebook" }
   ```
   If the Facebook backend runs separately from `API_MAIN`, set `VITE_FB_SHIELD_API`.
2. **Backend:** either run this service as-is, or mount `app.routers.facebook.router` in the main FastAPI app. Then replace `app.auth.current_user` with the main app's auth dependency (it must return `{"id": …}`, for example the company/user id). Point `DB_*` at the main database and run `migrate.py`. The tables are prefixed `fb_` and don't touch the `{prefix}tweets` tables.
3. Add `CORS_ORIGINS=https://finance.opcozero.com` if the service runs on its own host.

## 4. Tests

```powershell
cd backend
..\.venv\Scripts\python -m pytest -q
# MySQL integration tests (they DROP every table in that database, so use a throwaway one):
$env:TEST_DATABASE_URL="mysql+pymysql://user:pass@localhost:3306/fb_scam_shield_test"; ..\.venv\Scripts\python -m pytest -q
```
The suite covers:
- **Cookie import:** all three formats, every rejection message, and encryption round-trip and tampering.
- **In-page collector:** run in real Chrome against synthetic Facebook-like markup (text, links, image text, counts, "See more" expansion, loading shells, comments excluded).
- **Merging across scrolls**, detection rules, request validation, and auth.
- **Integration (MySQL):** import → start → cycle → feed/stats → session expiry → re-import → stop → switch account, encryption at rest, resume after restart, per-user isolation, and retention purge. These use a fake browser so they're deterministic.

## 5. Known limitations

- **Facebook markup changes.** Post discovery uses ARIA and `data-ad-*` hooks, not class names. If Facebook changes its markup, update `SELECTORS` in `backend/app/platforms/facebook/js/extractor.js`. In October 2026, Facebook removed the `role="feed"` wrapper, which stopped all scraping until the extractor was fixed. `tests/test_scraper_js_2026.py` models the current layout. If scraping returns 0 posts while the session is logged in, the markup has probably changed again.
- **Post dates.** Facebook hides them in obfuscated markup, so the timeline shows when the scraper first saw each post.
- **Engagement counts are best-effort** and may be missing on some posts.
- **Account restrictions.** Facebook may flag automated sessions; see docs/PRIVACY.md.
- **Detection is rules-only** until `GEMINI_API_KEY` is set. Scores are estimates, not verified findings. On the free Gemini tier, the per-minute quota (429) runs out during large scrapes. The scraper then switches to the next model in `GEMINI_FALLBACK_MODELS`, and scores rules-only only if every model fails. `check_facebook_config.py` makes a real Gemini call and shows the error.
- **Feed size.** Facebook only shows what your account follows. An account with few friends or pages reaches "No more posts" after about 10–30 posts, and the scraper stops there.
- The rate limiter and scraper workers are per process, so run a single backend worker.

## 6. Running it continuously (production on this PC)

`ops/supervisor.py` starts the backend and the built dashboard, waits for MySQL, applies migrations, restarts
on crashes or hangs, and backs up daily. `ops/install_autostart.ps1` starts it at logon, and `ops/deploy.py`
deploys a change with an automatic rollback. See:
- [docs/DEPLOYMENT_GUIDE.md](docs/DEPLOYMENT_GUIDE.md): setup, autostart, deploying, every environment variable.
- [docs/OPERATIONS_RUNBOOK.md](docs/OPERATIONS_RUNBOOK.md): health checks, logs, alerts, troubleshooting.
- [docs/RECOVERY_AND_ROLLBACK.md](docs/RECOVERY_AND_ROLLBACK.md): crash recovery, rollback, backup and restore.
- [docs/PRODUCTION_READINESS_AUDIT.md](docs/PRODUCTION_READINESS_AUDIT.md) and [docs/TEST_REPORT.md](docs/TEST_REPORT.md).
