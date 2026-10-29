#!/usr/bin/env python3
"""Restore a backup made by ops/backup.py.

    python ops/restore.py BACKUP_DIR --database fb_restore_check          # into a separate, empty database
    python ops/restore.py BACKUP_DIR --database fb_scam_shield --replace-live-database --restore-session-key

Safety rules:
  * The backup is verified (checksum + complete dump) before anything is changed.
  * A database that already has tables is only overwritten with --overwrite.
  * The live database (DB_NAME in backend/.env) is only touched with --replace-live-database,
    and a fresh backup of it is taken first. Stop the backend before doing that.
  * Row counts after the restore are compared with the counts recorded in the backup.
The target database must exist and DB_USER needs rights on it.
"""
from __future__ import annotations

import argparse
import gzip
import json
import pathlib
import re
import shutil
import subprocess
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from backup import _query, backup, table_counts, verify  # noqa: E402
from common import Paths, db_params, logger, mysql_env, mysql_tool, read_env  # noqa: E402

log = logger("restore")


def restore(folder: pathlib.Path, paths: Paths, database: str, overwrite=False, replace_live=False,
            restore_session_key=False) -> dict:
    problems = verify(folder)
    if problems:
        raise SystemExit(f"backup is not usable: {problems}")
    manifest = json.loads((folder / "manifest.json").read_text(encoding="utf-8"))
    params = dict(db_params(read_env(paths.env_file)), database=database)
    live = db_params(read_env(paths.env_file))["database"]
    if not re.fullmatch(r"\w+", database):
        raise SystemExit("database name may only contain letters, digits and _")
    if database == live and not replace_live:
        raise SystemExit(f"{database} is the live database: add --replace-live-database (stop the backend first)")
    existing = [r[0] for r in _query(params, "SHOW TABLES")]
    if existing and not overwrite and not replace_live:
        raise SystemExit(f"{database} already has {len(existing)} tables: add --overwrite to replace them")
    if database == live:
        log.info("taking a safety backup of the live database first")
        backup(paths)

    log.info("restoring %s into %s", folder.name, database)
    proc = subprocess.Popen([mysql_tool("mysql"), "-h", params["host"], "-P", params["port"], "-u", params["user"],
                             "--default-character-set=utf8mb4", database],
                            stdin=subprocess.PIPE, stderr=subprocess.PIPE, env=mysql_env(params))
    with gzip.open(folder / "database.sql.gz", "rb") as dump:
        shutil.copyfileobj(dump, proc.stdin)
    proc.stdin.close()
    err = proc.stderr.read().decode(errors="replace")
    if proc.wait() != 0:
        raise SystemExit(f"restore failed: {err.strip()}")

    after = table_counts(params)
    expected = manifest["tables"]
    mismatched = {t: (n, after.get(t)) for t, n in expected.items() if after.get(t) != n}
    if mismatched:
        raise SystemExit(f"row counts differ after restore (table: expected, got): {mismatched}")
    log.info("restored %d tables, %d rows; counts match the backup", len(after), sum(after.values()))

    if restore_session_key:
        src = folder / "session_key"
        if not src.exists():
            raise SystemExit("this backup has no session_key")
        if paths.session_key.exists():
            shutil.copy2(paths.session_key, paths.session_key.with_suffix(".before-restore"))
        shutil.copy2(src, paths.session_key)
        log.info("restored backend/.session_key (previous one kept as .session_key.before-restore)")
    return after


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("backup_dir")
    ap.add_argument("--database", required=True, help="target database (must exist)")
    ap.add_argument("--root", default=None, help="installation folder (default: this repository)")
    ap.add_argument("--overwrite", action="store_true", help="replace tables in a non-live database")
    ap.add_argument("--replace-live-database", action="store_true", help="allow restoring over DB_NAME")
    ap.add_argument("--restore-session-key", action="store_true", help="also put back backend/.session_key")
    a = ap.parse_args()
    restore(pathlib.Path(a.backup_dir), Paths(a.root) if a.root else Paths(), a.database,
            a.overwrite, a.replace_live_database, a.restore_session_key)


if __name__ == "__main__":
    main()
