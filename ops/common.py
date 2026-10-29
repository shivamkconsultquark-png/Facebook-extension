"""Shared paths and helpers for the ops scripts (backup, restore, supervisor, deploy).

Every script takes --root (default: this repository), so a staging copy can be operated on
without touching the live installation.
"""
from __future__ import annotations

import json
import logging
import logging.handlers
import os
import pathlib
import shutil
import subprocess
import sys
import urllib.parse

ROOT = pathlib.Path(__file__).resolve().parents[1]
MYSQL_BIN_CANDIDATES = [
    pathlib.Path(r"C:\Program Files\MySQL\MySQL Server 8.0\bin"),
    pathlib.Path(r"C:\Program Files\MySQL\MySQL Server 8.4\bin"),
]


OPS_SETTINGS = ("BACKEND_PORT", "BACKUP_DIR", "BACKUP_EVERY_HOURS", "BACKUP_KEEP", "MYSQL_BIN")


class Paths:
    def __init__(self, root: pathlib.Path | str = ROOT):
        self.root = pathlib.Path(root).resolve()
        self.backend = self.root / "backend"
        self.dashboard = self.root / "dashboard"
        self.env_file = self.backend / ".env"
        # Ops settings may live in backend/.env like everything else; the real environment wins.
        file_values = read_env(self.env_file)
        for key in OPS_SETTINGS:
            if key not in os.environ and file_values.get(key):
                os.environ[key] = file_values[key]
        self.session_key = self.backend / ".session_key"
        self.migrations = self.backend / "migrations"
        self.backups = pathlib.Path(os.getenv("BACKUP_DIR") or self.root / "backups")
        self.releases = self.root / "releases"
        self.run = self.root / "ops" / "run"
        self.logs = self.backend / "logs"

    @property
    def python(self) -> pathlib.Path:
        """The project's virtualenv Python (never the system one: the two drift apart)."""
        win = self.root / ".venv" / "Scripts" / "python.exe"
        return win if win.exists() else self.root / ".venv" / "bin" / "python"


def read_env(path: pathlib.Path) -> dict[str, str]:
    """KEY=VALUE lines of a .env file; the process environment wins, like the backend's own loading."""
    values: dict[str, str] = {}
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            values[k.strip()] = v.strip().strip('"').strip("'")
    for k in list(values):
        if k in os.environ:
            values[k] = os.environ[k]
    for k in ("DB_USER", "DB_PASS", "DB_HOST", "DB_PORT", "DB_NAME", "DATABASE_URL"):
        if k in os.environ:
            values[k] = os.environ[k]
    return values


def db_params(env: dict[str, str]) -> dict[str, str]:
    """host/port/user/password/database from DB_* or DATABASE_URL, as the backend resolves them."""
    if env.get("DB_USER") and env.get("DB_NAME"):
        return {"host": env.get("DB_HOST", "localhost"), "port": env.get("DB_PORT", "3306"),
                "user": env["DB_USER"], "password": env.get("DB_PASS", ""), "database": env["DB_NAME"]}
    url = urllib.parse.urlparse(env.get("DATABASE_URL", ""))
    if not url.hostname:
        raise SystemExit("Database not configured: set DB_* or DATABASE_URL in backend/.env")
    return {"host": url.hostname, "port": str(url.port or 3306), "user": urllib.parse.unquote(url.username or ""),
            "password": urllib.parse.unquote(url.password or ""), "database": url.path.lstrip("/")}


def mysql_tool(name: str) -> str:
    """mysqldump / mysql: MYSQL_BIN, then PATH, then the default MySQL install folders."""
    exe = name + (".exe" if os.name == "nt" else "")
    if os.getenv("MYSQL_BIN"):
        return str(pathlib.Path(os.environ["MYSQL_BIN"]) / exe)
    found = shutil.which(name)
    if found:
        return found
    for d in MYSQL_BIN_CANDIDATES:
        if (d / exe).exists():
            return str(d / exe)
    raise SystemExit(f"{name} not found: set MYSQL_BIN to the MySQL bin folder")


def mysql_env(params: dict[str, str]) -> dict[str, str]:
    """Password through MYSQL_PWD, never on the command line (visible in the process list)."""
    env = dict(os.environ)
    env["MYSQL_PWD"] = params["password"]
    return env


def run(cmd: list[str], **kw) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, check=True, **kw)


def logger(name: str, log_file: pathlib.Path | None = None) -> logging.Logger:
    log = logging.getLogger(name)
    if log.handlers:
        return log
    log.setLevel(logging.INFO)
    fmt = logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
    h = logging.StreamHandler(sys.stdout)
    h.setFormatter(fmt)
    log.addHandler(h)
    if log_file:
        log_file.parent.mkdir(parents=True, exist_ok=True)
        fh = logging.handlers.RotatingFileHandler(log_file, maxBytes=5 * 1024 * 1024, backupCount=5, encoding="utf-8")
        fh.setFormatter(fmt)
        log.addHandler(fh)
    return log


def write_json(path: pathlib.Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
    os.replace(tmp, path)  # atomic: a crash never leaves a half-written file
