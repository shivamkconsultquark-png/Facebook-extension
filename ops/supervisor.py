#!/usr/bin/env python3
"""Keep the backend (and optionally the built dashboard) running on this machine.

    python ops/supervisor.py                  # run in the foreground (Ctrl+C stops everything)
    python ops/supervisor.py --status         # what is running, restarts, last health check
    python ops/supervisor.py --restart        # graceful restart of the backend (used by deploy.py)
    python ops/supervisor.py --stop           # graceful stop of the backend and the supervisor

What it does:
  * One supervisor per installation (lock file); a second one exits immediately.
  * Waits for MySQL (bounded exponential backoff), backs up and applies pending migrations.
  * Starts uvicorn with the project's .venv Python, in a Windows Job Object so Chrome/Playwright
    child processes die with it instead of piling up after a crash.
  * Restarts the backend when it exits or stops answering /health/live (3 misses in a row),
    with backoff from 2 s up to 5 min. 5 crashes within 15 min is logged as an ALERT.
  * Stops the backend gracefully (Ctrl+Break: scrapers stop, then the process exits).
  * Backend output and its own log go to backend/logs/supervisor.log (rotated, 5 MB x 5).
  * Backs up the database every BACKUP_EVERY_HOURS (24), keeping BACKUP_KEEP (30) backups.
Autostart at logon: ops/install_autostart.ps1.
"""
from __future__ import annotations

import argparse
import ctypes
import datetime as dt
import json
import os
import pathlib
import signal
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from common import Paths, logger, write_json  # noqa: E402

IS_WIN = os.name == "nt"
LIVENESS_EVERY = 15  # seconds between /health/live checks
LIVENESS_MISSES = 3  # consecutive misses before the backend is considered hung
CRASH_WINDOW, CRASH_ALERT = 15 * 60, 5
BACKOFF_MIN, BACKOFF_MAX = 2.0, 300.0
STABLE_AFTER = 10 * 60  # a run this long resets the backoff


# ---------- Windows Job Object: children of the backend die with it
class _Job:
    def __init__(self):
        self.handle = None
        if not IS_WIN:
            return
        k32 = ctypes.windll.kernel32

        class LIMITS(ctypes.Structure):
            _fields_ = [("PerProcessUserTimeLimit", ctypes.c_int64), ("PerJobUserTimeLimit", ctypes.c_int64),
                        ("LimitFlags", ctypes.c_uint32), ("MinimumWorkingSetSize", ctypes.c_size_t),
                        ("MaximumWorkingSetSize", ctypes.c_size_t), ("ActiveProcessLimit", ctypes.c_uint32),
                        ("Affinity", ctypes.c_size_t), ("PriorityClass", ctypes.c_uint32),
                        ("SchedulingClass", ctypes.c_uint32)]

        class IO(ctypes.Structure):
            _fields_ = [(n, ctypes.c_uint64) for n in ("r", "w", "o", "rb", "wb", "ob")]

        class EXT(ctypes.Structure):
            _fields_ = [("Basic", LIMITS), ("Io", IO), ("ProcessMemoryLimit", ctypes.c_size_t),
                        ("JobMemoryLimit", ctypes.c_size_t), ("PeakProcessMemoryUsed", ctypes.c_size_t),
                        ("PeakJobMemoryUsed", ctypes.c_size_t)]

        k32.CreateJobObjectW.restype = ctypes.c_void_p
        k32.OpenProcess.restype = ctypes.c_void_p
        h = k32.CreateJobObjectW(None, None)
        info = EXT()
        info.Basic.LimitFlags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        k32.SetInformationJobObject(ctypes.c_void_p(h), 9, ctypes.byref(info), ctypes.sizeof(info))
        self.handle, self.k32 = h, k32

    def add(self, pid: int):
        if not self.handle:
            return
        ph = self.k32.OpenProcess(0x0101, False, pid)  # PROCESS_TERMINATE | PROCESS_SET_QUOTA
        if ph:
            self.k32.AssignProcessToJobObject(ctypes.c_void_p(self.handle), ctypes.c_void_p(ph))
            self.k32.CloseHandle(ctypes.c_void_p(ph))

    def close(self):
        """Kills every process still in the job (the backend's leftover children)."""
        if self.handle:
            self.k32.CloseHandle(ctypes.c_void_p(self.handle))
            self.handle = None


class Child:
    """One supervised process with restart backoff and crash-loop detection."""

    def __init__(self, name: str, cmd: list[str], cwd: pathlib.Path, log, env=None, live_url: str | None = None):
        self.name, self.cmd, self.cwd, self.log, self.env, self.live_url = name, cmd, cwd, log, env, live_url
        self.proc: subprocess.Popen | None = None
        self.job: _Job | None = None
        self.started_at = 0.0
        self.crashes: list[float] = []
        self.restarts = 0
        self.backoff = BACKOFF_MIN
        self.next_start = 0.0
        self.misses = 0
        self.last_check = 0.0
        self.last_exit = None
        self.alert = False

    def start(self):
        flags = subprocess.CREATE_NEW_PROCESS_GROUP if IS_WIN else 0
        self.proc = subprocess.Popen(self.cmd, cwd=self.cwd, env=self.env, stdout=subprocess.PIPE,
                                     stderr=subprocess.STDOUT, creationflags=flags, bufsize=1,
                                     text=True, encoding="utf-8", errors="replace")
        self.job = _Job()
        self.job.add(self.proc.pid)
        self.started_at, self.misses, self.last_check = time.time(), 0, time.time()
        threading.Thread(target=self._pump, args=(self.proc,), daemon=True).start()
        self.log.info("%s started (pid %s): %s", self.name, self.proc.pid, " ".join(self.cmd[-6:]))

    def _pump(self, proc):
        for line in proc.stdout:
            self.log.info("[%s] %s", self.name, line.rstrip())

    def running(self) -> bool:
        return self.proc is not None and self.proc.poll() is None

    def stop(self, timeout=30):
        if not self.running():
            self._cleanup()
            return
        self.log.info("stopping %s (pid %s) gracefully", self.name, self.proc.pid)
        try:
            if IS_WIN:
                self.proc.send_signal(signal.CTRL_BREAK_EVENT)
            else:
                self.proc.terminate()
            self.proc.wait(timeout)
        except subprocess.TimeoutExpired:
            self.log.warning("%s did not stop within %ss; killing it", self.name, timeout)
            self.proc.kill()
            self.proc.wait(10)
        self._cleanup()

    def _cleanup(self):
        if self.job:
            self.job.close()  # leftover Chrome / Playwright processes die here
            self.job = None

    def _live(self) -> bool:
        try:
            with urllib.request.urlopen(self.live_url, timeout=5) as r:
                return r.status == 200
        except (urllib.error.URLError, OSError):
            return False

    def tick(self, now: float):
        """Called every second: restart after an exit, a hang, or when the backoff delay has passed."""
        if self.proc is None:
            if now >= self.next_start:
                self.start()
            return
        code = self.proc.poll()
        if code is None and self.live_url and now - self.started_at > 30 and now - self.last_check >= LIVENESS_EVERY:
            self.last_check = now
            if self._live():
                self.misses = 0
            else:
                self.misses += 1
                self.log.warning("%s missed liveness check %d/%d", self.name, self.misses, LIVENESS_MISSES)
                if self.misses >= LIVENESS_MISSES:
                    self.log.error("%s is not answering: restarting it", self.name)
                    self.stop(timeout=10)
                    code = "hung"
        if code is None:
            if now - self.started_at > STABLE_AFTER:
                self.backoff, self.alert = BACKOFF_MIN, False  # stable again: clear the crash-loop alert
            return
        self._cleanup()
        self.last_exit = code
        self.crashes = [t for t in self.crashes if now - t < CRASH_WINDOW] + [now]
        if now - self.started_at > STABLE_AFTER:
            self.backoff = BACKOFF_MIN
        self.alert = len(self.crashes) >= CRASH_ALERT
        if self.alert:
            self.log.critical("ALERT: %s crashed %d times in %d min (last exit %s); check backend/logs",
                              self.name, len(self.crashes), CRASH_WINDOW // 60, code)
        self.log.error("%s exited (code %s); restarting in %.0f s", self.name, code, self.backoff)
        self.proc = None
        self.restarts += 1
        self.next_start = now + self.backoff
        self.backoff = min(self.backoff * 2, BACKOFF_MAX)

    def info(self) -> dict:
        return {"pid": self.proc.pid if self.running() else None, "restarts": self.restarts,
                "last_exit": self.last_exit, "crash_loop_alert": self.alert,
                "up_since": dt.datetime.fromtimestamp(self.started_at).isoformat(timespec="seconds")
                if self.running() else None}


class Supervisor:
    def __init__(self, paths: Paths, host: str, port: int, dashboard_port: int | None):
        self.paths, self.host, self.port, self.dashboard_port = paths, host, port, dashboard_port
        self.log = logger("supervisor", paths.logs / "supervisor.log")
        self.stop_requested = threading.Event()
        self.lock_file = None
        self.health = None

    # ---------- single instance
    def acquire_lock(self) -> bool:
        self.paths.run.mkdir(parents=True, exist_ok=True)
        self.lock_file = open(self.paths.run / "supervisor.lock", "a+")
        try:
            if IS_WIN:
                import msvcrt
                self.lock_file.seek(0)
                msvcrt.locking(self.lock_file.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(self.lock_file, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            return False
        return True

    # ---------- database + migrations before the first start
    def _backend_python(self, code: str, timeout=120) -> subprocess.CompletedProcess:
        return subprocess.run([str(self.paths.python), "-c", code], cwd=self.paths.backend,
                              capture_output=True, text=True, timeout=timeout)

    def wait_for_database(self):
        delay = 2.0
        code = "import sys; from app import db; sys.exit(0 if db.ping() else 1)"
        while not self.stop_requested.is_set():
            try:
                if self._backend_python(code, timeout=60).returncode == 0:
                    self.log.info("database reachable")
                    return True
            except subprocess.TimeoutExpired:
                pass
            self.log.warning("database not reachable yet; retrying in %.0f s", delay)
            self.stop_requested.wait(delay)
            delay = min(delay * 2, 60)
        return False

    def apply_migrations(self):
        out = self._backend_python("import json; from app import db; print(json.dumps(db.pending_migrations()))")
        try:
            pending = json.loads(out.stdout.strip().splitlines()[-1])
        except (ValueError, IndexError):
            self.log.error("could not read migration state: %s", out.stderr.strip()[-500:])
            return
        if not pending:
            return
        self.log.info("pending migrations %s: backing up first", pending)
        b = subprocess.run([str(self.paths.python), str(self.paths.root / "ops" / "backup.py"), "--root",
                            str(self.paths.root), "--keep", "30"], capture_output=True, text=True)
        if b.returncode != 0:
            self.log.error("backup failed, migrations NOT applied (backend starts degraded): %s", b.stderr.strip()[-500:])
            return
        m = subprocess.run([str(self.paths.python), "migrate.py"], cwd=self.paths.backend, capture_output=True, text=True)
        if m.returncode != 0:
            self.log.error("migration failed (backend starts degraded): %s", m.stderr.strip()[-1000:])
        else:
            self.log.info("migrations applied: %s", m.stdout.strip())

    def scheduled_backup(self):
        """Back up every BACKUP_EVERY_HOURS (default 24; 0 = off), keeping the newest BACKUP_KEEP (30)."""
        every = float(os.getenv("BACKUP_EVERY_HOURS", "24"))
        if every <= 0:
            return
        done = [p for p in self.paths.backups.glob("fbshield-*") if p.is_dir() and not p.name.endswith(".partial")]
        newest = max((p.stat().st_mtime for p in done), default=0)
        if time.time() - newest < every * 3600:
            return
        self.log.info("scheduled backup")
        r = subprocess.run([str(self.paths.python), str(self.paths.root / "ops" / "backup.py"), "--root",
                            str(self.paths.root), "--keep", os.getenv("BACKUP_KEEP", "30")],
                           capture_output=True, text=True)
        if r.returncode != 0:
            self.log.error("ALERT: scheduled backup failed: %s", (r.stdout + r.stderr).strip()[-500:])

    def _health(self):
        try:
            with urllib.request.urlopen(f"http://{self.host}:{self.port}/health", timeout=5) as r:
                return json.load(r)
        except urllib.error.HTTPError as e:
            try:
                return json.load(e)
            except ValueError:
                return {"status": f"http {e.code}"}
        except (urllib.error.URLError, OSError, ValueError):
            return None

    def write_status(self, children: list[Child]):
        h = self._health()
        if (h or {}).get("status") != (self.health or {}).get("status"):
            self.log.info("backend health: %s", (h or {}).get("status", "unreachable") if h else "unreachable")
        self.health = h
        write_json(self.paths.run / "status.json", {
            "supervisor_pid": os.getpid(), "updated": dt.datetime.now().isoformat(timespec="seconds"),
            "backend_url": f"http://{self.host}:{self.port}", "health": h,
            "children": {c.name: c.info() for c in children},
        })

    # ---------- main loop
    def run(self) -> int:
        if not self.acquire_lock():
            print("another supervisor is already running for this installation", file=sys.stderr)
            return 2
        for f in ("stop.request", "restart.request"):
            (self.paths.run / f).unlink(missing_ok=True)
        self.log.info("supervisor starting (pid %s) for %s", os.getpid(), self.paths.root)
        if not self.paths.python.exists():
            self.log.critical("no virtualenv Python at %s", self.paths.python)
            return 1
        if not self.wait_for_database():
            return 0
        self.apply_migrations()

        env = dict(os.environ, PYTHONUNBUFFERED="1", PYTHONIOENCODING="utf-8")
        backend = Child("backend", [str(self.paths.python), "-m", "uvicorn", "app.main:app", "--host", self.host,
                                    "--port", str(self.port), "--timeout-graceful-shutdown", "20"],
                        self.paths.backend, self.log, env, live_url=f"http://{self.host}:{self.port}/health/live")
        children = [backend]
        if self.dashboard_port:
            children.append(Child("dashboard", [str(self.paths.python), str(self.paths.root / "ops" / "serve_dashboard.py"),
                                                "--port", str(self.dashboard_port), "--root", str(self.paths.root)],
                                  self.paths.root, self.log, env))
        last_status, last_backup_check = 0.0, 0.0
        try:
            while not self.stop_requested.is_set():
                now = time.time()
                if (self.paths.run / "stop.request").exists():
                    self.log.info("stop requested")
                    break
                if (self.paths.run / "restart.request").exists():
                    (self.paths.run / "restart.request").unlink(missing_ok=True)
                    self.log.info("restart requested")
                    self.wait_for_database()
                    self.apply_migrations()
                    for c in children:
                        c.stop()
                        c.proc, c.next_start, c.backoff = None, 0.0, BACKOFF_MIN
                for c in children:
                    c.tick(now)
                if now - last_status >= LIVENESS_EVERY:
                    last_status = now
                    self.write_status(children)
                if now - last_backup_check >= 600:  # every 10 min: is a backup due?
                    last_backup_check = now
                    threading.Thread(target=self.scheduled_backup, daemon=True, name="backup").start()
                self.stop_requested.wait(1)
        except KeyboardInterrupt:
            self.log.info("interrupted")
        finally:
            for c in children:
                c.stop()
            (self.paths.run / "stop.request").unlink(missing_ok=True)
            write_json(self.paths.run / "status.json", {"supervisor_pid": None, "stopped":
                                                        dt.datetime.now().isoformat(timespec="seconds")})
            self.log.info("supervisor stopped")
        return 0


def _request(paths: Paths, name: str):
    paths.run.mkdir(parents=True, exist_ok=True)
    (paths.run / name).write_text(dt.datetime.now().isoformat(), encoding="utf-8")
    print(f"{name} written; the supervisor picks it up within a second")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--root", default=None)
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=None, help="backend port (default: BACKEND_PORT or 8001)")
    ap.add_argument("--dashboard-port", type=int, default=None, help="also serve dashboard/dist on this port")
    ap.add_argument("--status", action="store_true")
    ap.add_argument("--restart", action="store_true")
    ap.add_argument("--stop", action="store_true")
    a = ap.parse_args()
    paths = Paths(a.root) if a.root else Paths()
    a.port = a.port or int(os.getenv("BACKEND_PORT", "8001"))
    if a.status:
        f = paths.run / "status.json"
        print(f.read_text(encoding="utf-8") if f.exists() else "no status yet (supervisor never ran here)")
        return 0
    if a.restart:
        return _request(paths, "restart.request")
    if a.stop:
        return _request(paths, "stop.request")
    return Supervisor(paths, a.host, a.port, a.dashboard_port).run()


if __name__ == "__main__":
    sys.exit(main() or 0)
