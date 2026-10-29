"""The ops scripts: supervisor restart logic, process cleanup, backups, releases and rollback."""
import gzip
import hashlib
import json
import os
import pathlib
import subprocess
import sys
import time

import pytest

OPS = pathlib.Path(__file__).resolve().parents[2] / "ops"
sys.path.insert(0, str(OPS))

import backup as backup_mod  # noqa: E402
import deploy as deploy_mod  # noqa: E402
import restore as restore_mod  # noqa: E402
import supervisor as sup  # noqa: E402
from common import Paths  # noqa: E402


class FakeProc:
    def __init__(self, code=None):
        self.code, self.pid = code, 4242

    def poll(self):
        return self.code


class Log:
    def __init__(self):
        self.lines = []

    def __getattr__(self, level):
        return lambda msg, *a: self.lines.append((level, msg % a if a else msg))


# ---------- restart backoff and crash-loop alert
def test_backoff_doubles_up_to_the_cap_and_alerts_on_a_crash_loop():
    log = Log()
    c = sup.Child("backend", ["x"], pathlib.Path("."), log)
    delays, now = [], 1000.0
    for _ in range(9):
        c.proc, c.started_at = FakeProc(code=1), now  # crashes right after starting
        c.tick(now)
        delays.append(c.next_start - now)
        now += 1
    assert delays[:5] == [2, 4, 8, 16, 32]
    assert max(delays) == sup.BACKOFF_MAX
    assert c.alert and any(level == "critical" for level, _ in log.lines)
    assert c.restarts == 9


def test_a_long_stable_run_resets_backoff_and_clears_the_alert():
    c = sup.Child("backend", ["x"], pathlib.Path("."), Log())
    c.backoff, c.alert = 64.0, True
    c.proc, c.started_at = FakeProc(code=None), 0.0
    c.tick(sup.STABLE_AFTER + 1)
    assert c.backoff == sup.BACKOFF_MIN and c.alert is False


def test_crashes_outside_the_window_do_not_raise_the_alert():
    c = sup.Child("backend", ["x"], pathlib.Path("."), Log())
    now = 0.0
    for _ in range(sup.CRASH_ALERT):
        c.proc, c.started_at = FakeProc(code=1), now
        c.tick(now)
        now += sup.CRASH_WINDOW / 2 + 1  # crashes far apart
    assert not c.alert


# ---------- process cleanup
@pytest.mark.skipif(os.name != "nt", reason="Windows Job Objects")
def test_job_object_kills_the_childs_own_children(tmp_path):
    marker = tmp_path / "grandchild.pid"
    code = ("import subprocess, sys, time; "
            "g = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(120)']); "
            f"open(r'{marker}', 'w').write(str(g.pid)); time.sleep(120)")
    child = subprocess.Popen([sys.executable, "-c", code])
    job = sup._Job()
    job.add(child.pid)
    deadline = time.time() + 20
    while not marker.exists() and time.time() < deadline:
        time.sleep(0.2)
    grandchild = int(marker.read_text())
    child.kill()  # like a crash: the grandchild (think Chrome) would normally survive
    child.wait(10)
    job.close()
    time.sleep(1)
    alive = subprocess.run(["tasklist", "/FI", f"PID eq {grandchild}"], capture_output=True, text=True).stdout
    assert str(grandchild) not in alive


def test_only_one_supervisor_per_installation(tmp_path):
    (tmp_path / "ops").mkdir()
    a = sup.Supervisor(Paths(tmp_path), "127.0.0.1", 1, None)
    assert a.acquire_lock()
    script = (f"import sys; sys.path.insert(0, r'{OPS}'); import supervisor as s; from common import Paths; "
              f"print(s.Supervisor(Paths(r'{tmp_path}'), '127.0.0.1', 1, None).acquire_lock())")
    out = subprocess.run([sys.executable, "-c", script], capture_output=True, text=True, timeout=60)
    assert out.stdout.strip() == "False", out.stderr
    a.lock_file.close()


def test_scheduled_backup_runs_only_when_due(tmp_path, monkeypatch):
    s = sup.Supervisor(Paths(tmp_path), "127.0.0.1", 1, None)
    calls = []
    monkeypatch.setattr(sup.subprocess, "run", lambda *a, **k: calls.append(a) or subprocess.CompletedProcess(a, 0))
    monkeypatch.setenv("BACKUP_DIR", str(tmp_path / "b"))
    s.paths = Paths(tmp_path)
    s.scheduled_backup()
    assert len(calls) == 1  # no backup yet: due
    (tmp_path / "b" / "fbshield-20260101-000000").mkdir(parents=True)
    s.scheduled_backup()
    assert len(calls) == 1  # fresh backup exists: not due
    monkeypatch.setenv("BACKUP_EVERY_HOURS", "0")
    os.utime(tmp_path / "b" / "fbshield-20260101-000000", (0, 0))
    s.scheduled_backup()
    assert len(calls) == 1  # disabled


# ---------- backups
def _fake_backup(folder: pathlib.Path, body: bytes, session_key=True):
    folder.mkdir(parents=True)
    with gzip.open(folder / "database.sql.gz", "wb") as f:
        f.write(body)
    if session_key:
        (folder / "session_key").write_text("k")
    digest = hashlib.sha256((folder / "database.sql.gz").read_bytes()).hexdigest()
    (folder / "manifest.json").write_text(json.dumps({"dump_sha256": digest, "session_key": session_key, "tables": {}}))


def test_verify_accepts_a_complete_backup(tmp_path):
    _fake_backup(tmp_path / "ok", b"CREATE TABLE t (id int);\n-- Dump completed on 2026-10-28\n")
    assert backup_mod.verify(tmp_path / "ok") == []


def test_verify_rejects_truncated_damaged_or_incomplete_backups(tmp_path):
    _fake_backup(tmp_path / "trunc", b"CREATE TABLE t (id int);\nINSERT INTO t VALUES (1")
    assert any("incomplete" in p for p in backup_mod.verify(tmp_path / "trunc"))
    _fake_backup(tmp_path / "dmg", b"x\n-- Dump completed\n")
    with open(tmp_path / "dmg" / "database.sql.gz", "ab") as f:
        f.write(b"garbage")
    assert any("checksum" in p for p in backup_mod.verify(tmp_path / "dmg"))
    _fake_backup(tmp_path / "nokey", b"x\n-- Dump completed\n")
    (tmp_path / "nokey" / "session_key").unlink()
    assert any("session_key" in p for p in backup_mod.verify(tmp_path / "nokey"))
    (tmp_path / "empty").mkdir()
    assert backup_mod.verify(tmp_path / "empty")


def test_prune_keeps_the_newest_backups(tmp_path, monkeypatch):
    monkeypatch.setenv("BACKUP_DIR", str(tmp_path))
    for i in range(5):
        (tmp_path / f"fbshield-2026010{i}-000000").mkdir()
    (tmp_path / "fbshield-20260109-000000.partial").mkdir()
    backup_mod.prune(Paths(tmp_path), keep=2)
    assert sorted(p.name for p in tmp_path.iterdir()) == ["fbshield-20260103-000000", "fbshield-20260104-000000",
                                                         "fbshield-20260109-000000.partial"]


def test_restore_refuses_the_live_database_and_non_empty_targets(tmp_path, monkeypatch):
    _fake_backup(tmp_path / "bk", b"x\n-- Dump completed\n")
    (tmp_path / "backend").mkdir()
    (tmp_path / "backend" / ".env").write_text("DB_USER=u\nDB_PASS=p\nDB_NAME=live_db\n")
    for k in ("DB_USER", "DB_PASS", "DB_NAME", "DB_HOST", "DB_PORT", "DATABASE_URL"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setattr(restore_mod, "_query", lambda params, sql: [["some_table"]])
    with pytest.raises(SystemExit, match="live database"):
        restore_mod.restore(tmp_path / "bk", Paths(tmp_path), "live_db")
    with pytest.raises(SystemExit, match="--overwrite"):
        restore_mod.restore(tmp_path / "bk", Paths(tmp_path), "other_db")
    with pytest.raises(SystemExit, match="letters, digits"):
        restore_mod.restore(tmp_path / "bk", Paths(tmp_path), "x; DROP DATABASE live_db")


# ---------- releases and rollback
def _tree(root: pathlib.Path, version: str):
    (root / "backend" / "app").mkdir(parents=True, exist_ok=True)
    (root / "backend" / "migrations").mkdir(exist_ok=True)
    (root / "backend" / "app" / "config.py").write_text(f'APP_VERSION = "{version}"\n')
    (root / "backend" / "migrations" / "001_initial.sql").write_text("CREATE TABLE t (id int);")
    (root / "backend" / ".env").write_text("DB_PASS=secret\n")
    (root / "backend" / ".session_key").write_text("key")


def test_snapshot_never_contains_secrets_and_rollback_removes_new_files(tmp_path):
    _tree(tmp_path, "1.0.0")
    d = deploy_mod.Deployer(Paths(tmp_path), 1)
    good = d.snapshot("good")
    import zipfile
    names = zipfile.ZipFile(good).namelist()
    assert "backend/app/config.py" in names
    assert not any(n.endswith(".env") or "session_key" in n for n in names)

    # a bad release: new version, a new module and a new migration
    (tmp_path / "backend" / "app" / "config.py").write_text('APP_VERSION = "2.0.0"\n')
    (tmp_path / "backend" / "app" / "new_module.py").write_text("raise RuntimeError\n")
    (tmp_path / "backend" / "migrations" / "002_bad.sql").write_text("BROKEN")
    failed = d.snapshot("failed")
    assert d.restore_last_good() == "1.0.0"
    assert 'APP_VERSION = "1.0.0"' in (tmp_path / "backend" / "app" / "config.py").read_text()
    assert not (tmp_path / "backend" / "app" / "new_module.py").exists()
    assert not (tmp_path / "backend" / "migrations" / "002_bad.sql").exists()
    assert "backend/app/new_module.py" in zipfile.ZipFile(failed).namelist()  # the new work is kept
    assert (tmp_path / "backend" / ".env").read_text() == "DB_PASS=secret\n"  # secrets untouched


def test_rollback_without_a_good_release_is_refused(tmp_path):
    _tree(tmp_path, "1.0.0")
    with pytest.raises(deploy_mod.DeployError, match="no last known good"):
        deploy_mod.Deployer(Paths(tmp_path), 1).restore_last_good()


def test_old_releases_are_pruned(tmp_path):
    _tree(tmp_path, "1.0.0")
    d = deploy_mod.Deployer(Paths(tmp_path), 1)
    for i in range(deploy_mod.KEEP_RELEASES + 2):
        f = d.snapshot("failed")
        os.utime(f, (i, i))
        f.rename(f.with_name(f"failed-1.0.0-{i:04d}.zip"))
    d.snapshot("failed")
    assert len(list((tmp_path / "releases").glob("failed-*.zip"))) == deploy_mod.KEEP_RELEASES


def test_ops_settings_are_read_from_backend_env(tmp_path, monkeypatch):
    (tmp_path / "backend").mkdir()
    (tmp_path / "backend" / ".env").write_text(f"BACKUP_DIR={tmp_path / 'elsewhere'}\nBACKEND_PORT=9123\n")
    monkeypatch.delenv("BACKUP_DIR", raising=False)
    monkeypatch.delenv("BACKEND_PORT", raising=False)
    p = Paths(tmp_path)
    assert p.backups == tmp_path / "elsewhere" and os.environ["BACKEND_PORT"] == "9123"
    monkeypatch.setenv("BACKUP_DIR", str(tmp_path / "from_env"))  # the real environment wins
    assert Paths(tmp_path).backups == tmp_path / "from_env"
