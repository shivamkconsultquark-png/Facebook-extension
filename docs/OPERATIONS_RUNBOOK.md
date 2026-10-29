# Operations runbook

## Daily use
| Task | Command (PowerShell, project folder) |
|---|---|
| Start everything | `powershell -ExecutionPolicy Bypass -File ops\start_supervisor.ps1` (automatic at logon once installed) |
| Status | `.venv\Scripts\python ops\supervisor.py --status` |
| Restart backend | `.venv\Scripts\python ops\supervisor.py --restart` |
| Stop everything | `.venv\Scripts\python ops\supervisor.py --stop` |
| Deploy a change | `.venv\Scripts\python ops\deploy.py` |
| Back up now | `.venv\Scripts\python ops\backup.py` |
| Config check | `cd backend; ..\.venv\Scripts\python check_facebook_config.py` |
| Migrations | `cd backend; ..\.venv\Scripts\python migrate.py --status` |

Dashboard: http://localhost:5173/facebook · API: http://localhost:8001

## Health checks
| URL | Meaning | Healthy |
|---|---|---|
| `GET /health/live` | Process answers (no DB). The supervisor restarts the backend after 3 misses. | 200 `{"status":"ok","version":…}` |
| `GET /health` | Ready to serve | 200 with `status: ok`. 503 if the DB is down, migrations are pending or the config is invalid |

`/health` also reports `version`, `migrations_pending`, `config_problems`, `scrapers.running`,
`scrapers.dead_workers`, `disk_free_mb` and `warnings` (low disk, dead scraper thread).
Quick check: `curl.exe -s http://localhost:8001/health`.

## Logs
| File | Contents | Rotation |
|---|---|---|
| `backend\logs\backend.log` | Application log: startup/shutdown with version and pid, scrape cycles, errors, purges | 10 MB × 10 |
| `backend\logs\supervisor.log` | Starts, exits, restarts, health changes, ALERTs, backups, plus all backend output incl. uvicorn errors and request lines | 5 MB × 6 |
| `backend\logs\deploy.log` | Every deploy step and rollback | 5 MB × 6 |

Find problems: `Select-String -Path backend\logs\*.log -Pattern "ERROR|CRITICAL|ALERT" | Select-Object -Last 30`.
Logs never contain passwords, tokens or cookies. `ConnectionResetError` tracebacks from asyncio are harmless
Windows noise (audit L2).

## Alerts to act on
| Sign | Meaning | Do |
|---|---|---|
| `ALERT: backend crashed 5 times in 15 min` | Crash loop | Read the traceback above it in `supervisor.log`; fix or `deploy.py --rollback` |
| `ALERT: scheduled backup failed` | No fresh backup | Run `ops\backup.py` by hand and read the error (MySQL down? disk full?) |
| `/health` 503 `migrations_pending` | Code newer than the schema | `cd backend; ..\.venv\Scripts\python migrate.py` (after a backup) |
| `/health` 503 `database: false` | MySQL down | RECOVERY_AND_ROLLBACK §6 |
| `warnings: low disk space` | Under 1 GB free | Free space; move `BACKUP_DIR` |
| `config_problems` not empty | Invalid `.env` value | Fix `backend\.env`, then `--restart` |

There is no external alerting (email/SMS). Alerts live in the logs and in `--status`. Check them weekly, or after
the PC was off.

## Troubleshooting
| Symptom | Check | Fix |
|---|---|---|
| Dashboard: "Can't reach the Facebook Filter server" | `supervisor.py --status`; `curl.exe http://localhost:8001/health/live` | Start the supervisor; read `supervisor.log` |
| Dashboard won't load at all | Is 5173 served? `curl.exe -I http://localhost:5173/facebook` | `dashboard\dist` missing → `npm run build`; port busy (Vite dev server running?) |
| "another supervisor is already running" | `--status` | It is already running; use `--restart` |
| Port 8001 in use at start | Old hand-started uvicorn | Close it (DEPLOYMENT_GUIDE §3) |
| Deploy says "no last known good release" | First deploy | `ops\deploy.py --snapshot` while the app works |
| Deploy rolled back | `backend\logs\deploy.log` shows why | The project folder now holds the last good code. Your edits are in `releases\failed-*.zip`: unzip it to a scratch folder, fix, copy back, deploy again |
| Scraper "Login required" | Facebook session expired | Import a session / log in |
| CPU or memory high | Task Manager: chrome.exe count | `SCRAPER_MAX_BROWSERS`; `--restart` kills leftover browsers |

## Weekly and monthly
- **Weekly**: `--status` (restarts, `crash_loop_alert`), the ALERT search above, `disk_free_mb`.
- **Monthly**: test-restore the newest backup into a scratch database (RECOVERY_AND_ROLLBACK §4); copy
  `backups\` and `backend\.env` to another disk.
- **When releasing**: always through `ops\deploy.py`.
