# Test report

Run on 28 October 2026 on this PC: Windows 10 Pro 19045, Python 3.10.11 (`.venv`), Node 18+, Chrome, MySQL 8.0.40.
Only results that were actually executed and read are listed. **PASS** = verified, **FAIL** = tested and failed,
**NOT VERIFIED** = not tested.

## Test isolation
Your live database was never written to by a test. Tests and experiments used a **separate MySQL 8.0.40 instance**
started from the installed binaries, with its own data folder in the temp directory and port 3307
(`mysqld --initialize-insecure`, root, no password, localhost only). It held:
- `fbs_test`: the integration suite (dropped and recreated by the fixture).
- `fbs_stage`: a **copy of your real data**, restored from a backup, used for the upgrade and recovery experiments.
- `fbs_restore`: the restore check.

A **staging copy** of the project, with ports 8011 (API) and 5181 (dashboard) and `SCRAPER_RESUME=false`, was
used for supervisor and deploy experiments, so your running backend on 8001 and dashboard on 5173 were not touched.
Staging could not use your Facebook session: different encryption key, scraping off.

The only operations against the live database were **read-only**: two `mysqldump --single-transaction` backups,
`CHECKSUM TABLE` and `SELECT`s.

## 1. Automated tests

Commands (from `backend/`):
```powershell
..\.venv\Scripts\python -m pytest -q                                   # default
$env:TEST_DATABASE_URL="mysql+pymysql://root:@127.0.0.1:3307/fbs_test"; $env:E2E="1"
..\.venv\Scripts\python -m pytest -q                                   # everything
```

| Run | Result |
|---|---|
| Baseline before changes, default | 84 passed, 8 skipped |
| Baseline, MySQL integration (`test_api_mysql.py`) on the isolated DB | 8 passed |
| After changes, everything (MySQL + E2E) | **125 passed**, 0 failed, 1 deprecation warning (L6) |
| After changes, default (no test DB) | **110 passed, 15 skipped** (MySQL and E2E tests skip) |
| Stability: full MySQL run 3 times in a row | 107 / 107 / 107 passed (before the E2E and ops tests were added) |

During the work, the full run **failed once with a MySQL deadlock** (error 1213) between two retention purges.
That became finding H6. It was fixed and re-run: green 3 times.

New test files:

| File | Tests | Covers |
|---|---|---|
| `tests/test_reliability.py` | 18 | Worker survives DB errors; resume retries with capped backoff and stops on shutdown; dead-thread detection; `/health/live` without DB; `/health` 503 when DB down or migrations pending, 200 when ready; config validation; `.env` found from another folder; failed logins recorded (MySQL); audit retention (MySQL); purge retries a deadlock; 8 identical concurrent registrations create 1 account; storing the same posts concurrently leaves no duplicates; 6 concurrent Start requests run 1 scraper |
| `tests/test_ops.py` | 14 | Backoff 2→4→8→16→32 up to 300 s and the crash-loop alert; alert cleared after a stable run; Job Object kills a crashed backend's own child process; one supervisor per installation; scheduled-backup timing; backup verification rejects truncated, damaged and incomplete dumps; pruning; restore refuses the live DB, non-empty targets and bad names; release zips never contain `.env`/`.session_key`; rollback removes files the bad release added and keeps them in the failed zip; old releases pruned; ops settings read from `backend/.env` |
| `tests/test_e2e_dashboard.py` | 1 | **Real Chrome → built dashboard → uvicorn → MySQL**: load and default "Select Range", Type filter through the API, direct navigation to `/facebook/auto-scraper`, backend killed → error banner with posts kept → backend back → recovers by itself |

## 2. Builds and dependency checks

| Check | Result |
|---|---|
| `npx vite build` (dashboard, production) | PASS |
| `pip check` (in deploy preflight) | PASS |
| `python -m compileall app` | PASS |
| `pip-audit` (48 installed packages) | PASS: no known vulnerabilities |
| `npm audit --omit=dev` | PASS: 0 |
| `npm audit` (incl. dev) | 2 advisories in vite/esbuild, dev server only (M12, accepted) |
| PowerShell parse of `install_autostart.ps1` | PASS: 0 errors |

## 3. Recovery and operations experiments (staging, isolated MySQL)

| # | Scenario | What was done | Result |
|---|---|---|---|
| 1 | Normal start | Supervisor started: waited for DB, backed up, applied migration 009, started backend and dashboard | **PASS**: healthy, version 1.1.0, 0 pending |
| 2 | Reboot / boot order | MySQL stopped, supervisor started first, MySQL started 20 s later | **PASS**: waited (retries after 2, 4, 8 s), did not start a doomed backend, healthy 7 s after MySQL |
| 3 | Forced termination | `taskkill /F` on the backend | **PASS**: answering again in 6 s; old process tree gone (Job Object) |
| 4 | Hung backend | Real server process suspended (`NtSuspendProcess`) | **PASS**: 3 missed liveness checks (about 45 s), graceful stop timed out, killed, new backend healthy. The first attempt suspended the venv launcher instead of the server; that was a test mistake, not a supervisor failure (see note) |
| 5 | Crash loop | Code changed to raise at import | **PASS**: restarts after 8, 16, 32 s, then `ALERT … crashed 5 times in 15 min`; healthy 10 s after the code was fixed |
| 6 | Database outage | Isolated MySQL shut down for about 60 s | **PASS**: `/health/live` 200, `/health` 503 `database=false`, **0 backend restarts**, dashboard still served, healthy 3 s after MySQL returned |
| 7 | Invalid configuration | Unreachable DB at start (6); unit tests for invalid values | **PASS**: waits instead of crash-looping; `config_problems` reported in the log and `/health` |
| 8 | Failed deploy: bad migration | Version 1.1.2 with invalid SQL | **PASS**: stopped at step 5, rolled back to 1.1.1, healthy in 7 s, migration not recorded, new code in `failed-1.1.2-*.zip` |
| 9 | Failed deploy: passes tests, crashes in production | Version 1.1.3 that only fails outside pytest | **PASS**: tests passed, health check failed, rolled back. First run waited the full 120 s; after the fast-fail change it was detected after 3 crashes, 33 s from restart to healthy 1.1.1 |
| 10 | Successful upgrade with existing data | Real-data copy at migration 008 → new code (009) | **PASS**: all row counts equal, `fb_post_analyses` checksum equal to the original |
| 11 | Rollback to previous version | Scenarios 8 and 9 | **PASS** |
| 12 | Backup and restore | Live DB backed up; restored into the isolated server | **PASS**: 10 tables, 1013 rows, counts match. `CHECKSUM TABLE` matches for every table except `api_tokens`, whose only difference is `last_used_at` (updated live by your open dashboard after the backup). Tampered backup refused; restore over the live DB and over a non-empty DB refused without flags |
| 13 | Repeated requests / duplicate events | Concurrency tests (section 1) | **PASS** |
| 14 | Frontend while backend is down and after | E2E test | **PASS** |
| 15 | Graceful stop | `supervisor.py --stop` | **PASS**: "stopping scrapers" → "Finished server process" → port free, in under 1 s, including from the hidden console the logon launcher uses |
| 16 | Autostart launcher | Exact Startup-shortcut command (hidden PowerShell, started from `C:\`) | **PASS**: backend and dashboard healthy 6 s after launch |
| 17 | Single instance | Second supervisor started | **PASS**: exit code 2 |
| 18 | Data after all experiments | Counts and checksum vs the original backup | **PASS**: unchanged; broken migration never applied |

Note on scenario 4: on Windows, `.venv\Scripts\python.exe` is a launcher that starts the real Python as a child.
Suspending the launcher left the server answering. This also showed that, without the Job Object, killing the
launcher would leave an orphaned server holding the port. The Job Object closes that gap; see scenario 3 and
`test_job_object_kills_the_childs_own_children`.

## 4. Not verified
| Item | Why | How to verify |
|---|---|---|
| Real reboot of this PC | Not done without your permission | DEPLOYMENT_GUIDE §5, "After a reboot" |
| Startup-folder shortcut installed on your account | Changes your logon; needs your OK | `ops\install_autostart.ps1`, then log off and on |
| Supervisor running your live installation | Your hand-started backend (8001) and dashboard (5173) are still running | DEPLOYMENT_GUIDE §3 |
| A real scrape under the supervisor | Staging could not use your Facebook session (by design) | Start the scraper from the dashboard after switching |
| finance.opcozero.com integration | No access | README §3 |
| Browser extension | Removed from the project earlier | n/a |
| MySQL80 restart-on-failure | Needs admin | RECOVERY_AND_ROLLBACK §6 |
| Python 3.12 | Not attempted (M8) | Planned change |
