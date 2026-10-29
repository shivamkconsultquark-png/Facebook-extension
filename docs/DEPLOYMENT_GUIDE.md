# Deployment guide

Target: one Windows PC running MySQL 8, with the backend and dashboard started at logon and kept running by
`ops/supervisor.py`. All commands run in PowerShell from the project folder `C:\Users\hp\Facebook extension`.

## 1. Requirements
- Windows 10/11, logged-on user (the Facebook login window needs your desktop).
- MySQL 8.0 running as a Windows service (`MySQL80`, start type Automatic).
- Python 3.10+ in `.venv` (3.10 is end of life; plan 3.12, audit M8), Google Chrome, Node 18+ and npm (dashboard build).
- About 1 GB free disk minimum; `/health` warns below that.

## 2. First installation
```powershell
python -m venv .venv
.venv\Scripts\python -m pip install -r backend\requirements.lock   # exact tested versions
#   (backend\requirements.txt has the version ranges; use it only to upgrade on purpose)
Copy-Item backend\.env.example backend\.env                         # then edit: DB_*, GEMINI_API_KEY, …
cd backend; ..\.venv\Scripts\python migrate.py; ..\.venv\Scripts\python check_facebook_config.py; cd ..
cd dashboard; npm ci; npm run build; cd ..                           # dashboard\.env: VITE_FB_SHIELD_API=http://localhost:8001
.venv\Scripts\python ops\backup.py                                   # first backup (also proves mysqldump works)
.venv\Scripts\python ops\deploy.py --snapshot                        # record this version as "last known good"
```
The database user needs these rights on its own database: `ALL PRIVILEGES ON fb_scam_shield.*` (it already has them).

## 3. Switching from hand-started processes to the supervisor
Today the backend runs in a terminal on 8001 (system Python) and the Vite dev server runs on 5173. The supervisor
uses the same ports, so stop those first:
1. Close the terminal running `uvicorn … --port 8001`, or press Ctrl+C in it. Running scrapers resume under
   the supervisor, because their state is in the database.
2. Stop the Vite dev server (`npm run dev`) on 5173.
3. Start: `powershell -ExecutionPolicy Bypass -File ops\start_supervisor.ps1`
4. Check: `.venv\Scripts\python ops\supervisor.py --status` shows `"status": "ok"`; http://localhost:5173/facebook loads.

Need the Vite dev server while developing? Start the supervisor without the dashboard:
`ops\start_supervisor.ps1 -DashboardPort 0`.

## 4. Start automatically at logon
```powershell
powershell -ExecutionPolicy Bypass -File ops\install_autostart.ps1              # install
powershell -ExecutionPolicy Bypass -File ops\install_autostart.ps1 -Uninstall   # remove
```
This adds "Facebook Filter supervisor" to your Startup folder; no admin rights needed. MySQL starts at boot as a
service, and the supervisor waits for it.
**Limitation:** it runs while you are logged on. After a reboot it starts when you log in, not before.

## 5. Deploying a change
1. Edit the code in this folder. Bump `APP_VERSION` in `backend/app/config.py`; deploy checks for the new version.
2. Write schema changes as a new `backend/migrations/NNN_name.sql`, **additive only**: add tables, columns
   (nullable or with a default) and indexes. Never rename or drop in the same release. Do that later, once no
   running version uses the old column (expand → migrate → contract).
3. Keep API responses backward compatible: add fields, don't rename or remove them. The dashboard pages copied
   into finance.opcozero.com may be an older version than the backend.
4. Run `.venv\Scripts\python ops\deploy.py`. It runs, in order:
   preflight (`pip check`, compile, config, DB) → tests → dashboard build (into `dist.new`, swapped in) →
   backup → migrations → graceful restart → `/health` must report the new version. Then it records the
   version as last known good. On failure, it saves your code to `releases\failed-*.zip`, restores the last good
   code, restarts and checks health (RECOVERY_AND_ROLLBACK §3).
5. Changed `requirements.txt`? Install first and re-lock:
   `.venv\Scripts\pip install -r backend\requirements.txt; .venv\Scripts\pip freeze > backend\requirements.lock`.
   Deploy rollback does not downgrade packages.

**After a reboot:** log on, wait about 30 s, then run `.venv\Scripts\python ops\supervisor.py --status` and open http://localhost:8001/health.

## 6. Environment variables (`backend/.env`)
The real environment overrides the file. Secrets are marked 🔒: never commit or share them.

| Variable | Default | Purpose |
|---|---|---|
| `DB_USER`, `DB_PASS`🔒, `DB_HOST`, `DB_PORT`, `DB_NAME` | –, –, localhost, 3306, – | MySQL connection (required) |
| `DATABASE_URL`🔒 | – | Alternative to `DB_*`, used only when `DB_USER`/`DB_NAME` are unset |
| `GEMINI_API_KEY`🔒 | empty | Gemini scoring; empty = rules only |
| `GEMINI_MODEL`, `GEMINI_FALLBACK_MODELS` | flash-lite, … | Model and fallbacks |
| `LLM_TIMEOUT_MS`, `LLM_WORKERS` | 10000, 4 | Gemini timeout; posts scored in parallel |
| `SAFE_BROWSING_API_KEY`🔒 | empty | Optional URL reputation lookups |
| `STORE_POST_TEXT`, `POST_TEXT_MAX_CHARS` | true, 1000 | Store post excerpts |
| `RETENTION_DAYS`, `FEEDBACK_RETENTION_DAYS`, `AUDIT_RETENTION_DAYS` | 30, 180, 365 | Data retention |
| `MAX_POSTS_PER_REQUEST`, `MAX_TEXT_CHARS`, `RATE_LIMIT_POSTS_PER_MINUTE` | 20, 5000, 600 | Abuse limits |
| `ALLOW_REGISTRATION`, `TOKEN_TTL_DAYS` | true, 30 | Accounts and sessions |
| `CORS_ORIGINS` | – | Allowed dashboard origins, comma-separated |
| `SESSION_ENCRYPTION_KEY`🔒 | empty → `backend/.session_key` | Encrypts stored Facebook cookies. **Back it up; losing it loses every linked session** |
| `SCRAPER_HEADLESS`, `SCRAPER_BROWSER_CHANNEL` | true, chrome | Browser for scraping |
| `SCRAPER_SCROLLS`, `SCRAPER_MAX_POSTS`, `SCRAPER_REELS`, `SCRAPER_MAX_ALBUMS`, `SCRAPER_ALBUM_MAX` | 25, 60, 10, 5, 30 | Work per scrape cycle |
| `SCRAPER_DEFAULT_INTERVAL`, `SCRAPER_MAX_BROWSERS` | 300, 2 | Cycle interval, concurrent browsers (1–8) |
| `SCRAPER_ALLOW_LOGIN_WINDOW`, `SCRAPER_LOGIN_TIMEOUT` | true, 300 | "Log in to Facebook" window |
| `SCRAPER_RESUME` | true | Resume scrapers after restart. **false on any second instance** sharing the DB |
| `LOG_DIR`, `LOG_LEVEL` | logs, INFO | `backend/logs/backend.log` (10 MB × 10); empty dir = console only |
| `BACKEND_PORT` | 8001 | Port for the supervisor and deploy health checks |
| `BACKUP_DIR` | `<project>\backups` | Backup folder. Point it at another disk or a synced folder |
| `BACKUP_EVERY_HOURS`, `BACKUP_KEEP` | 24, 30 | Automatic backups by the supervisor (0 = off) |
| `MYSQL_BIN` | auto | MySQL `bin` folder if `mysqldump` isn't found |
| `TEST_DATABASE_URL`🔒, `E2E` | – | Tests only: a throwaway database (it is wiped) and the browser E2E test |

Dashboard (`dashboard/.env`): `VITE_FB_SHIELD_API`, the backend URL baked into the build.

## 7. Files the supervisor and deploy create
| Path | What | Back up? |
|---|---|---|
| `backups\fbshield-*` | DB dump, session key, manifest | Yes, to another disk |
| `releases\good-*.zip`, `failed-*.zip`, `last_good.json` | Code snapshots (no secrets) | Optional |
| `backend\logs\*.log` | Rotated logs | No |
| `ops\run\status.json`, `supervisor.lock` | Live state | No |
| `dashboard\dist`, `dist.prev` | Current and previous dashboard build | No |
