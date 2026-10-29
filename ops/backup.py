#!/usr/bin/env python3
"""Back up the database and the session encryption key.

    python ops/backup.py                 # backups/fbshield-YYYYmmdd-HHMMSS/
    python ops/backup.py --keep 14       # then delete all but the newest 14 backups
    python ops/backup.py --verify DIR    # check an existing backup

Each backup folder holds:
  database.sql.gz   mysqldump --single-transaction (consistent while the backend keeps writing)
  session_key       backend/.session_key: without it the stored Facebook sessions can't be decrypted
  manifest.json     time, app version, applied migrations, row count per table, SHA-256 of the dump

backend/.env is NOT copied (it holds passwords); keep your own copy of it somewhere safe.
"""
from __future__ import annotations

import argparse
import datetime as dt
import gzip
import hashlib
import pathlib
import re
import shutil
import subprocess
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from common import Paths, db_params, logger, mysql_env, mysql_tool, read_env, write_json  # noqa: E402

log = logger("backup")
DONE_MARKER = b"-- Dump completed"


def _sha256(path: pathlib.Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _query(params: dict, sql: str) -> list[list[str]]:
    out = subprocess.run([mysql_tool("mysql"), "-h", params["host"], "-P", params["port"], "-u", params["user"],
                          "--batch", "--skip-column-names", params["database"], "-e", sql],
                         env=mysql_env(params), capture_output=True, text=True, check=True)
    return [line.split("\t") for line in out.stdout.splitlines() if line]


def table_counts(params: dict) -> dict[str, int]:
    tables = [r[0] for r in _query(params, "SHOW TABLES")]
    if not tables:
        return {}
    union = " UNION ALL ".join(f"SELECT '{t}', COUNT(*) FROM `{t}`" for t in tables if re.fullmatch(r"\w+", t))
    return {name: int(n) for name, n in _query(params, union)}


def backup(paths: Paths, keep: int | None = None) -> pathlib.Path:
    params = db_params(read_env(paths.env_file))
    stamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    target = paths.backups / f"fbshield-{stamp}"
    partial = target.with_name(target.name + ".partial")
    partial.mkdir(parents=True)
    dump = partial / "database.sql.gz"
    log.info("dumping %s@%s:%s/%s", params["user"], params["host"], params["port"], params["database"])
    cmd = [mysql_tool("mysqldump"), "-h", params["host"], "-P", params["port"], "-u", params["user"],
           "--single-transaction", "--quick", "--routines", "--triggers", "--no-tablespaces",
           "--set-gtid-purged=OFF", "--default-character-set=utf8mb4", params["database"]]
    with gzip.open(dump, "wb") as out:
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=mysql_env(params))
        shutil.copyfileobj(proc.stdout, out)
        err = proc.stderr.read().decode(errors="replace")
        if proc.wait() != 0:
            shutil.rmtree(partial, ignore_errors=True)
            raise SystemExit(f"mysqldump failed: {err.strip()}")
    if paths.session_key.exists():
        shutil.copy2(paths.session_key, partial / "session_key")
    else:
        log.warning("no backend/.session_key (sessions use SESSION_ENCRYPTION_KEY from .env; back that up yourself)")
    counts = table_counts(params)
    migrations = ([r[0] for r in _query(params, "SELECT version FROM schema_migrations ORDER BY version")]
                  if "schema_migrations" in counts else [])  # a brand-new database has none yet
    write_json(partial / "manifest.json", {
        "created": dt.datetime.now().isoformat(timespec="seconds"),
        "database": params["database"], "host": params["host"],
        "migrations": migrations, "tables": counts,
        "dump_sha256": _sha256(dump), "session_key": (partial / "session_key").exists(),
    })
    partial.rename(target)  # only complete backups get the final name
    problems = verify(target)
    if problems:
        raise SystemExit(f"backup {target} failed verification: {problems}")
    log.info("backup complete: %s (%.1f MB)", target, (target / "database.sql.gz").stat().st_size / 1e6)
    if keep:
        prune(paths, keep)
    return target


def verify(folder: pathlib.Path) -> list[str]:
    """Problems with a backup folder (empty list = usable)."""
    import json
    problems = []
    manifest_path, dump = folder / "manifest.json", folder / "database.sql.gz"
    if not manifest_path.exists() or not dump.exists():
        return ["manifest.json or database.sql.gz missing"]
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if _sha256(dump) != manifest.get("dump_sha256"):
        problems.append("dump checksum mismatch (file damaged)")
    try:
        with gzip.open(dump, "rb") as f:
            tail = b""
            for chunk in iter(lambda: f.read(1 << 20), b""):
                tail = (tail + chunk)[-4096:]
        if DONE_MARKER not in tail:
            problems.append("dump is incomplete (no 'Dump completed' trailer)")
    except (OSError, EOFError) as e:
        problems.append(f"dump unreadable: {e}")
    if manifest.get("session_key") and not (folder / "session_key").exists():
        problems.append("session_key missing")
    return problems


def prune(paths: Paths, keep: int) -> None:
    done = sorted(p for p in paths.backups.glob("fbshield-*") if p.is_dir() and not p.name.endswith(".partial"))
    for old in done[:-keep]:
        shutil.rmtree(old)
        log.info("deleted old backup %s", old.name)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--root", default=None, help="installation folder (default: this repository)")
    ap.add_argument("--keep", type=int, default=None, help="keep only the newest N backups")
    ap.add_argument("--verify", metavar="DIR", help="verify an existing backup folder and exit")
    a = ap.parse_args()
    if a.verify:
        problems = verify(pathlib.Path(a.verify))
        print("OK" if not problems else "\n".join(problems))
        sys.exit(1 if problems else 0)
    backup(Paths(a.root) if a.root else Paths(), a.keep)


if __name__ == "__main__":
    main()
