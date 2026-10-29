# Production readiness audit

Audit date: 28 October 2026. Scope: everything in `C:\Users\hp\Facebook extension`.
Status: **Fixed** (changed and verified by a test or experiment), **Mitigated** (risk reduced and documented),
**Open** (not changed: needs your approval, admin rights or a planned upgrade), **Accepted** (low risk, documented).

## 1. What the project actually is

The audit brief assumed some things that are not true of this repository. Checked on the machine:

| Brief said | Actually |
|---|---|
| Database is SQLite | **MySQL 8.0.40**, local Windows service `MySQL80` (auto-start), database `fb_scam_shield`, user `fbshield` (rights on that database only). No SQLite anywhere. |
| `extension`, `extension-tests`, `tools` folders | **Do not exist.** The browser extension was removed earlier at your request; Facebook scraping runs inside the backend (Playwright driving the installed Chrome). Extension checks are therefore **not applicable**. |
| Git status, branch | **Not a git repository.** There is no history to diff or roll back to (see M11). |
| CI/CD, Docker, deploy scripts | **None existed.** |

Components and where they run:

| Component | Technology | Runs | Before this audit |
|---|---|---|---|
| Backend API + scrapers + retention purge | Python 3.10, FastAPI, SQLAlchemy/PyMySQL, Playwright + Chrome | Continuously, on this PC | Started by hand in a terminal (`uvicorn … --port 8001`), with the **system** Python, not `.venv` |
| Database | MySQL 8.0.40 Windows service | Continuously | Auto-starts at boot; no Windows restart-on-failure actions |
| Dashboard | React 18 + Vite 5 harness (`dashboard/`) | In your browser; served from this PC | Vite **dev** server on 5173, started by hand |
| Production frontend | The two pages are meant to be copied into finance.opcozero.com | finance.opcozero.com | Not in this repository; no access (NOT VERIFIED) |

**Deployment target confirmed: this Windows 10 PC, one user, no admin rights in this session.** The Facebook
login window must appear on your desktop, so the backend has to run in your logged-on session. That is why
autostart is a logon item and not a Windows service.

Baseline before any change: `pytest` **84 passed, 8 skipped**. The 8 MySQL integration tests had never run on
this machine because `fbshield` cannot create a test database. With an isolated MySQL instance (see TEST_REPORT)
they ran: **8 passed**. Dashboard build: OK. `npm audit`: 0 production vulnerabilities. `pip-audit`: 0 known vulnerabilities.

## 2. Findings

### Critical
None found.

### High

| # | Finding | Evidence | Impact | Status |
|---|---|---|---|---|
| H1 | `.env` was loaded relative to the **current folder** (`load_dotenv()` with no path) | Started from `C:\`: `settings.database_url` empty, CORS list empty | Any service, scheduled task or shortcut that starts elsewhere runs with no database and no CORS | **Fixed**: `backend/app/config.py` loads `backend/.env` by absolute path. Test `test_env_file_is_found_from_any_working_directory` |
| H2 | **Scraper thread died on any database error** | In `Worker.cycle` the DB calls before and after the `try` were unprotected; an exception ended `run()` while the status still said "running" | A MySQL hiccup silently stopped scraping until the backend was restarted | **Fixed**: `Worker.run` catches, logs and retries next cycle; `/health` lists dead scraper threads. Test `test_worker_keeps_running_when_the_database_fails` |
| H3 | **Scrapers were not resumed if MySQL was not ready at startup** | `resume_all()` was called once; on error it only logged a warning | After a reboot (backend starting before MySQL) running scrapers silently never came back | **Fixed**: `resume_in_background` retries with backoff 2 s → 60 s. Tests + boot-ordering experiment |
| H4 | **No backups and no restore procedure**; the session encryption key existed in one copy | No backup scripts or folder; `backend/.session_key` only | A disk failure or bad migration loses all data; losing the key makes every linked Facebook session undecryptable | **Fixed**: `ops/backup.py` (consistent dump + key + manifest + checksum), `ops/restore.py` (verified, guarded), daily automatic backup in the supervisor. Real data backed up and restored, every table compared |
| H5 | **No automatic start or crash recovery** | Backend and dashboard were terminal processes | Reboot, logoff, a crash or a closed terminal stopped everything until restarted by hand | **Fixed** (install needs your OK): `ops/supervisor.py` + `ops/install_autostart.ps1`. Kill, hang, crash loop, DB outage and boot order verified |
| H6 | **Retention purge could deadlock** with user requests | The full test run hit MySQL error 1213; the purge ran five DELETEs in one transaction, two on unindexed date columns | A login or a scraper's save could fail at random when the purge ran | **Fixed**: each purge in its own short transaction, retried once on deadlock; migration `009_retention_indexes.sql` (additive). Suite then passed 3 runs in a row |

### Medium

| # | Finding | Evidence | Status |
|---|---|---|---|
| M1 | Failed logins were **never recorded**: the audit row was written inside the transaction that the 401 rolled back | `auth_routes.login` | **Fixed**. Test `test_failed_logins_are_recorded` |
| M2 | When the backend was unreachable, the dashboard showed **"No posts found"** | `fetchData` only logged to the console | **Fixed**: banner "Can't reach the server…", posts kept on screen, retry every 15 s and a Retry button. Browser E2E test |
| M3 | `/health` only pinged the DB: no version, migrations, config, scraper or disk state; no liveness/readiness split | `app/main.py` | **Fixed**: `/health/live` (process) and `/health` (readiness: DB, migrations, config, scraper threads, free disk, version) |
| M4 | Logs only went to the terminal: lost when it was closed, no rotation | `logging.basicConfig` | **Fixed**: `backend/logs/backend.log` 10 MB × 10; supervisor and deploy logs 5 MB × 6 |
| M5 | `audit_events` grew forever | Not in the purge | **Fixed**: `AUDIT_RETENTION_DAYS` (365) |
| M6 | Python dependencies were unpinned (`>=`), so a fresh install could pull untested versions | `requirements.txt` | **Mitigated**: `backend/requirements.lock` records the 48 exact versions tested here |
| M7 | Backend ran with the system Python, the tests with `.venv`: two environments that drift | Process list: `Python310\python.exe -m uvicorn …` | **Fixed** for supervised runs: the supervisor and deploy always use `.venv` |
| M8 | **Python 3.10 reached end of life in October 2026** (no more security fixes) | `python --version` → 3.10.11 | **Open**: plan a move to Python 3.12 as its own tested change |
| M9 | A second backend sharing the database resumes the same scrapers: two Chromes on one Facebook account. This happened earlier in this project | `SCRAPER_RESUME` defaults to true | **Mitigated**: one supervisor per installation (lock file), tests force `SCRAPER_RESUME=false`, documented. Separate installations on one database are still possible |
| M10 | The `MySQL80` service has no restart-on-failure actions | `sc qfailure MySQL80`: no actions | **Open** (needs admin): command in RECOVERY_AND_ROLLBACK §6 |
| M11 | Not a git repository | `git status`: not a repository | **Mitigated**: deploy snapshots every good release and keeps failed code; `git init` recommended |
| M12 | npm advisories in vite/esbuild (moderate + high) | `npm audit` | **Accepted**: dev server only (`npm audit --omit=dev` → 0); the supervisor serves the built files instead. Fix needs vite 6 (breaking) |

### Low

| # | Finding | Status |
|---|---|---|
| L1 | Deprecated `HTTP_422_UNPROCESSABLE_ENTITY` warnings | **Fixed** |
| L2 | Windows asyncio prints `ConnectionResetError` tracebacks when a client drops a connection | **Accepted**: noise only |
| L3 | FastAPI `/docs` is enabled | **Accepted**: bound to 127.0.0.1 |
| L4 | `mysqldump` gets the password through `MYSQL_PWD` (visible to the same Windows user) | **Accepted**: never on the command line |
| L5 | Backups default to the same disk; the disk is 78 % full (31 GB free) | **Mitigated**: `/health` warns under 1 GB; set `BACKUP_DIR` to another disk or a synced folder |
| L6 | Starlette `TestClient` deprecation warning | **Accepted**: test-only |

## 3. Areas checked with no problem found
- **Secrets**: none in code; `.env`, `.session_key`, backups and releases are git-ignored; `.env.example` holds placeholders only; release zips exclude `.env` and `.session_key` (test).
- **Auth**: PBKDF2-SHA256 (310 000 iterations), constant-time compare, tokens stored as SHA-256, expiry, login rate limit; every `/facebook/*` route requires a token and filters by `user_id` (isolation test).
- **SQL injection**: every query uses bound parameters; the only formatted SQL is a whitelisted sort or WHERE clause.
- **Input**: request validation, 512 KB body limit, validation errors never echo values back.
- **CORS**: explicit origins, no credentials, limited methods and headers.
- **Logs**: no cookies, tokens or passwords logged.
- **Indexes**: the feed's filters and sorts are covered; date indexes added for the purge (H6).
- **Idempotency**: post upserts on a unique (user, post) key; scraper start is idempotent per user (concurrency tests).

## 4. Changed files

| File | Why |
|---|---|
| `backend/app/config.py` | H1 absolute `.env` path; `APP_VERSION`; `LOG_DIR`, `LOG_LEVEL`, `AUDIT_RETENTION_DAYS`; `config_problems()` |
| `backend/app/main.py` | M4 rotating log file; startup/shutdown messages with version; H3 background resume; M3 `/health/live` and the readiness `/health`; H6 purge in short transactions |
| `backend/app/db.py` | `pending_migrations()` for health and the supervisor |
| `backend/app/platforms/facebook/scraper.py` | H2 worker survives errors; H3 `resume_in_background`; `health()` |
| `backend/app/repository.py` | H6 `purge_expired_separately` with deadlock retry; M5 audit retention |
| `backend/app/routers/auth_routes.py` | M1 failed logins recorded |
| `backend/app/routers/facebook.py` | L1 deprecated constant |
| `backend/migrations/009_retention_indexes.sql` | H6: two indexes, additive |
| `backend/requirements.lock` (new) | M6 exact tested versions |
| `backend/.env.example` | Missing variables (`DATABASE_URL`, `LLM_WORKERS`, `SCRAPER_RESUME`) and the new log/ops settings; placeholders only |
| `backend/tests/conftest.py` | Tests never write to `backend/logs` and never resume real scrapers |
| `backend/tests/test_api_mysql.py` | The resume test enables resume explicitly |
| `backend/tests/test_reliability.py`, `test_ops.py`, `test_e2e_dashboard.py` (new) | Section 1 of TEST_REPORT |
| `dashboard/src/pages/FacebookInsights.jsx` | M2 outage banner, automatic retry, error empty state |
| `ops/common.py`, `backup.py`, `restore.py`, `supervisor.py`, `serve_dashboard.py`, `deploy.py`, `start_supervisor.ps1`, `install_autostart.ps1` (new) | H4, H5, deployment and rollback |
| `.gitignore` | Runtime folders: logs, backups, releases, `ops/run`, dashboard build swaps |
| `docs/*.md` | This audit, TEST_REPORT, DEPLOYMENT_GUIDE, RECOVERY_AND_ROLLBACK, OPERATIONS_RUNBOOK; API.md health section |

Created on disk by the work (git-ignored): `backups/` (two real backups of the live DB).
No change was made to the live database, your running processes, Windows services or your Startup folder.

## 5. Readiness verdict
- **Local Windows deployment (this PC): ready once you approve** installing autostart and switching from the hand-started processes to the supervisor. Everything else was verified (TEST_REPORT).
- **Integration into finance.opcozero.com: NOT VERIFIED**. No access to that app, its auth or its server.
- Before relying on it long-term: back up to another disk (L5), plan Python 3.12 (M8), and set MySQL recovery actions (M10).
