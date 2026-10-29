# Recovery and rollback

## 1. What recovers by itself (verified, see TEST_REPORT §3)
| Failure | Automatic behaviour |
|---|---|
| Backend crashes or is killed | Supervisor restarts it after 2 s, doubling each time up to 5 min; reset after 10 min stable. Leftover Chrome/Playwright processes are killed with it (Job Object). |
| Backend hangs | 3 missed `/health/live` checks (about 45 s) → graceful stop, kill if needed, restart. |
| Repeated crashes | Still retried with backoff. `ALERT … crashed 5 times in 15 min` in `backend/logs/supervisor.log`, and `crash_loop_alert: true` in `supervisor.py --status`. |
| MySQL down or restarting | Backend stays up; `/health` returns 503 (`database: false`); healthy again within seconds of MySQL returning. Scrape cycles that fail are retried next cycle. The dashboard shows "Can't reach…" or keeps the last posts and retries every 15 s. |
| MySQL not ready at boot | Supervisor waits (2 s → 60 s backoff); scrapers resume once the DB answers. |
| Deployment fails (tests, migration or health) | Nothing restarts before the tests pass. After a restart, failure → automatic rollback to the last good release (§3). |
| Facebook session expired | Scraper keeps running and shows "Login required"; import a fresh session. |
| A scrape cycle fails (network, Facebook change) | Logged; retried next interval. |

Not automatic: a damaged database (§4), a lost disk (§5), MySQL refusing to start (§6), and anything while you are logged off.

## 2. Restart by hand
```powershell
.venv\Scripts\python ops\supervisor.py --status     # state, restarts, last health
.venv\Scripts\python ops\supervisor.py --restart    # graceful backend restart
.venv\Scripts\python ops\supervisor.py --stop       # stop backend, dashboard and supervisor
powershell -ExecutionPolicy Bypass -File ops\start_supervisor.ps1   # start again
```
Supervisor itself gone (closed, crashed)? Start it again; the lock file stops a second copy from starting.

## 3. Roll back code
`deploy.py` rolls back by itself on failure. To roll back by hand (for example, a problem found an hour later):
```powershell
.venv\Scripts\python ops\deploy.py --rollback
```
This saves the current code to `releases\failed-<version>-<time>.zip`, restores the last good release, restarts
and waits for health. To go further back, unzip an older `releases\good-*.zip` over the project folder (it holds
no secrets or data), then run `ops\supervisor.py --restart`.

The **database is not rolled back**: migrations are additive, so older code runs against the newer schema
(verified in TEST_REPORT §3, scenarios 8 and 9). Restore a backup (§4) only if a migration damaged data.

## 4. Restore the database
Backups: `backups\fbshield-YYYYmmdd-HHMMSS\` (daily, newest 30, plus one before every deploy and migration).

**Check a backup** (run this monthly): restore it into a scratch database. The live one stays untouched.
```powershell
.venv\Scripts\python ops\backup.py --verify backups\fbshield-20261028-125746
# needs an empty database the DB user can write to, e.g. created by a MySQL admin:
#   CREATE DATABASE fb_restore_check; GRANT ALL ON fb_restore_check.* TO 'fbshield'@'localhost';
.venv\Scripts\python ops\restore.py backups\fbshield-20261028-125746 --database fb_restore_check
```
It compares row counts with the backup's manifest and stops if any differ.

**Replace the live database** (data loss since that backup, so only when needed):
```powershell
.venv\Scripts\python ops\supervisor.py --stop
.venv\Scripts\python ops\restore.py backups\fbshield-YYYYmmdd-HHMMSS --database fb_scam_shield --replace-live-database
#   add --restore-session-key if backend\.session_key was lost or replaced
powershell -ExecutionPolicy Bypass -File ops\start_supervisor.ps1
```
`restore.py` takes a safety backup of the live database before overwriting it.

## 5. Rebuild on a new PC
1. Install MySQL 8, Python, Chrome and Node; copy the project folder (or a `releases\good-*.zip`).
2. Create the database and user (DEPLOYMENT_GUIDE §2), and copy your saved `backend\.env`. It is **not** in backups.
3. `CREATE DATABASE fb_scam_shield`, then `ops\restore.py <backup> --database fb_scam_shield --replace-live-database --restore-session-key`.
4. `migrate.py`, then `ops\start_supervisor.ps1`, then `ops\install_autostart.ps1`.
Without the session key, the data is still restored, but linked Facebook accounts must import their session again.

## 6. MySQL won't start, or keeps stopping
- Windows Services → MySQL80 → Start. Logs: `C:\ProgramData\MySQL\MySQL Server 8.0\Data\*.err`.
- Ask Windows to restart it on failure (**admin PowerShell**; not done, needs your approval):
  `sc.exe failure MySQL80 reset= 86400 actions= restart/60000/restart/60000/restart/300000`
- Disk full is the most common cause: `/health` → `disk_free_mb`.

## 7. Scraper-specific recovery
| Symptom | Fix |
|---|---|
| Status "Login required" | Import a fresh session or use "Log in to Facebook". |
| Two scrapers on one account | Only one backend may run with `SCRAPER_RESUME=true` on this database. Stop the extra one, then `--restart`. |
| `/health` → `"warnings": ["scraper thread stopped unexpectedly"]` | `ops\supervisor.py --restart`; report the traceback from `backend.log`. |
| 0 posts while logged in | Facebook changed its markup (README §5). |
