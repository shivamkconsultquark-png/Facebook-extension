#!/usr/bin/env python3
"""Deploy the code in this folder to the running installation, with automatic rollback.

    python ops/deploy.py --snapshot     # once: record the running version as "last known good"
    python ops/deploy.py                # validate, test, build, back up, migrate, restart, health-check
    python ops/deploy.py --rollback     # put back the last known good version by hand

Steps (stops at the first failure; nothing is restarted before step 6):
  1. Preflight: .venv Python, `pip check`, every backend module compiles, configuration valid, MySQL reachable.
  2. Tests: pytest (MySQL integration tests too when TEST_DATABASE_URL is set).
  3. Dashboard: `npm run build` into dashboard/dist.new, then swapped in (old one kept as dist.prev).
  4. Database backup (ops/backup.py, newest 30 kept).
  5. Migrations (additive only; older code keeps working with the new schema).
  6. Graceful restart through the supervisor, then /health must report the new version within 120 s.
  7. Success: the code is snapshotted to releases/ as the new "last known good" (newest 5 kept).
     Failure: the new code is saved to releases/failed-*.zip, the last known good code is restored and
     restarted. The database is NOT rolled back (migrations are additive); restore a backup only if a
     migration damaged data (docs/RECOVERY_AND_ROLLBACK.md).
Requires the supervisor to be running (ops/supervisor.py) for steps 6-7.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import pathlib
import re
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
import zipfile

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from common import Paths, logger, write_json  # noqa: E402

KEEP_RELEASES = 5
HEALTH_TIMEOUT = 120
# What a release consists of. Secrets, data, logs, backups and installed packages are never included.
RELEASE_GLOBS = ["backend/app/**/*.py", "backend/app/**/*.js", "backend/migrations/*.sql", "backend/*.py",
                 "backend/requirements*.txt", "backend/.env.example", "ops/*.py", "ops/*.ps1",
                 "dashboard/src/**/*", "dashboard/index.html", "dashboard/package.json",
                 "dashboard/package-lock.json", "dashboard/vite.config.js", "dashboard/dist/**/*"]
EXCLUDE = re.compile(r"(__pycache__|\.pyc$|/\.env$|\.session_key)")


class DeployError(Exception):
    pass


class Deployer:
    def __init__(self, paths: Paths, port: int, run_tests=True):
        self.p, self.port, self.run_tests = paths, port, run_tests
        self.log = logger("deploy", paths.logs / "deploy.log")
        self.base = f"http://127.0.0.1:{port}"

    # ---------- helpers
    def sh(self, cmd, cwd=None, env=None, timeout=1800, shell=False):
        self.log.info("$ %s", " ".join(map(str, cmd)))
        r = subprocess.run(cmd, cwd=cwd or self.p.root, env=env, capture_output=True, text=True,
                           timeout=timeout, shell=shell, encoding="utf-8", errors="replace")
        if r.returncode != 0:
            raise DeployError(f"{cmd[0]} failed (exit {r.returncode}):\n{(r.stdout + r.stderr)[-3000:]}")
        return r.stdout

    def py(self, *args, **kw):
        return self.sh([str(self.p.python), *args], **kw)

    def code_version(self) -> str:
        src = (self.p.backend / "app" / "config.py").read_text(encoding="utf-8")
        m = re.search(r'APP_VERSION\s*=\s*"([^"]+)"', src)
        return m.group(1) if m else "unknown"

    def health(self):
        try:
            with urllib.request.urlopen(f"{self.base}/health", timeout=5) as r:
                return r.status, json.load(r)
        except urllib.error.HTTPError as e:
            try:
                return e.code, json.load(e)
            except ValueError:
                return e.code, {}
        except (urllib.error.URLError, OSError, ValueError):
            return None, {}

    def supervisor_running(self) -> bool:
        f = self.p.run / "status.json"
        if not f.exists():
            return False
        try:
            return bool(json.loads(f.read_text(encoding="utf-8")).get("supervisor_pid"))
        except ValueError:
            return False

    # ---------- steps
    def preflight(self):
        self.log.info("1/7 preflight")
        if not self.p.python.exists():
            raise DeployError(f"no virtualenv at {self.p.python}")
        self.py("-m", "pip", "check")
        self.py("-m", "compileall", "-q", "app", cwd=self.p.backend)
        out = self.py("-c", "import json; from app.config import config_problems; from app import db; "
                            "print(json.dumps({'problems': config_problems(), 'db': db.ping()}))", cwd=self.p.backend)
        state = json.loads(out.strip().splitlines()[-1])
        if state["problems"]:
            raise DeployError(f"configuration problems: {state['problems']}")
        if not state["db"]:
            raise DeployError("database not reachable")

    def tests(self):
        if not self.run_tests:
            self.log.warning("2/7 tests SKIPPED (--skip-tests)")
            return
        self.log.info("2/7 tests")
        self.py("-m", "pytest", "-q", "-p", "no:cacheprovider", cwd=self.p.backend, timeout=3600)

    def build_dashboard(self):
        self.log.info("3/7 dashboard build")
        npm = shutil.which("npm") or shutil.which("npm.cmd")
        if not npm:
            raise DeployError("npm not found")
        d = self.p.dashboard
        shutil.rmtree(d / "dist.new", ignore_errors=True)
        self.sh([npm, "run", "build", "--", "--outDir", "dist.new", "--emptyOutDir"], cwd=d, shell=os.name == "nt")
        if not (d / "dist.new" / "index.html").exists():
            raise DeployError("dashboard build produced no index.html")
        shutil.rmtree(d / "dist.prev", ignore_errors=True)
        if (d / "dist").exists():
            os.replace(d / "dist", d / "dist.prev")
        os.replace(d / "dist.new", d / "dist")

    def backup(self):
        self.log.info("4/7 database backup")
        self.py(str(self.p.root / "ops" / "backup.py"), "--root", str(self.p.root), "--keep", "30")

    def migrate(self):
        self.log.info("5/7 migrations")
        self.log.info(self.py("migrate.py", cwd=self.p.backend).strip())

    def restart_and_check(self, expect_version: str) -> dict:
        self.log.info("6/7 restart and health check (expecting version %s)", expect_version)
        self.p.run.mkdir(parents=True, exist_ok=True)
        restarts_before = self.backend_restarts()
        (self.p.run / "restart.request").write_text(dt.datetime.now().isoformat(), encoding="utf-8")
        time.sleep(3)
        deadline, last = time.time() + HEALTH_TIMEOUT, None
        while time.time() < deadline:
            code, body = self.health()
            last = (code, body)
            if code == 200 and body.get("version") == expect_version and not (self.p.run / "restart.request").exists():
                self.log.info("healthy: version %s", expect_version)
                return body
            # The new version keeps crashing: don't wait out the whole timeout.
            if self.backend_restarts() - restarts_before >= 3:
                raise DeployError(f"version {expect_version} crashed 3 times after the restart; see backend/logs/supervisor.log")
            time.sleep(2)
        raise DeployError(f"not healthy within {HEALTH_TIMEOUT}s; last /health: {last}")

    def backend_restarts(self) -> int:
        try:
            st = json.loads((self.p.run / "status.json").read_text(encoding="utf-8"))
            return int(st["children"]["backend"]["restarts"])
        except (OSError, ValueError, KeyError, TypeError):
            return 0

    # ---------- releases
    def _files(self):
        seen = set()
        for g in RELEASE_GLOBS:
            for f in self.p.root.glob(g):
                rel = f.relative_to(self.p.root).as_posix()
                if f.is_file() and rel not in seen and not EXCLUDE.search("/" + rel):
                    seen.add(rel)
                    yield f, rel

    def snapshot(self, kind="good") -> pathlib.Path:
        self.p.releases.mkdir(parents=True, exist_ok=True)
        stamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
        target = self.p.releases / f"{kind}-{self.code_version()}-{stamp}.zip"
        tmp = target.with_suffix(".partial")
        with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as z:
            for f, rel in self._files():
                z.write(f, rel)
        os.replace(tmp, target)
        if kind == "good":
            write_json(self.p.releases / "last_good.json", {"file": target.name, "version": self.code_version(),
                                                            "created": stamp})
        same_kind = sorted(self.p.releases.glob(f"{kind}-*.zip"), key=lambda f: f.stat().st_mtime)
        for old in same_kind[:-KEEP_RELEASES]:
            old.unlink()
        self.log.info("snapshot %s", target.name)
        return target

    def restore_last_good(self) -> str:
        meta = self.p.releases / "last_good.json"
        if not meta.exists():
            raise DeployError("no last known good release (run deploy.py --snapshot while the app works)")
        info = json.loads(meta.read_text(encoding="utf-8"))
        z = self.p.releases / info["file"]
        with zipfile.ZipFile(z) as zf:
            names = set(zf.namelist())
            # Files the bad release added must go: a new module would still be imported, and an
            # unapplied new migration would make /health report "pending". (They are in failed-*.zip;
            # migrations that were applied stay in the database and are harmless to older code.)
            for f, rel in list(self._files()):
                if rel not in names:
                    f.unlink()
            zf.extractall(self.p.root)
        self.log.info("restored %s (version %s)", info["file"], info["version"])
        return info["version"]

    # ---------- the whole deployment
    def deploy(self) -> int:
        new_version = self.code_version()
        self.log.info("deploying version %s from %s", new_version, self.p.root)
        if not self.supervisor_running():
            raise DeployError("the supervisor is not running (start ops/supervisor.py first)")
        if not (self.p.releases / "last_good.json").exists():
            raise DeployError("no last known good release to roll back to: run deploy.py --snapshot first")
        self.preflight()
        self.tests()
        self.build_dashboard()
        self.backup()
        try:
            self.migrate()
            self.restart_and_check(new_version)
        except DeployError as e:
            self.log.error("deployment failed: %s", e)
            self.rollback()
            return 1
        self.log.info("7/7 recording version %s as last known good", new_version)
        self.snapshot("good")
        self.log.info("deployment of %s complete", new_version)
        return 0

    def rollback(self):
        failed = self.snapshot("failed")
        self.log.warning("new code saved to %s; rolling back", failed)
        version = self.restore_last_good()
        if (self.p.dashboard / "dist.prev" / "index.html").exists():
            shutil.rmtree(self.p.dashboard / "dist", ignore_errors=True)
            os.replace(self.p.dashboard / "dist.prev", self.p.dashboard / "dist")
        self.restart_and_check(version)
        self.log.warning("rolled back to %s and healthy. Fix the code from %s and deploy again.", version, failed.name)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--root", default=None)
    ap.add_argument("--port", type=int, default=None, help="backend port (default: BACKEND_PORT or 8001)")
    ap.add_argument("--snapshot", action="store_true", help="record the current code as last known good")
    ap.add_argument("--rollback", action="store_true", help="restore the last known good code and restart")
    ap.add_argument("--skip-tests", action="store_true")
    a = ap.parse_args()
    paths = Paths(a.root) if a.root else Paths()
    d = Deployer(paths, a.port or int(os.getenv("BACKEND_PORT", "8001")), run_tests=not a.skip_tests)
    try:
        if a.snapshot:
            d.snapshot("good")
            return 0
        if a.rollback:
            d.rollback()
            return 0
        return d.deploy()
    except DeployError as e:
        d.log.error("%s", e)
        return 1


if __name__ == "__main__":
    sys.exit(main())
