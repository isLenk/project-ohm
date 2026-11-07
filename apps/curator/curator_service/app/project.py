"""The open .ohm project: a SQLite file edited through a working copy.

Every write commits to the working copy. Save writes it to the real path with
VACUUM INTO and an atomic rename, so a crash mid-save never corrupts the file.
"""

import hashlib
import json
import os
import shutil
import sqlite3
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator, Optional

from . import schema
from .config import DATA_DIR
from .errors import ApiError

WORK_DIR = DATA_DIR / "work"
WORK_DIR.mkdir(parents=True, exist_ok=True)


def now_ms() -> int:
    return int(time.time() * 1000)


def _work_path(path: Path) -> Path:
    key = hashlib.sha1(str(path.resolve()).encode()).hexdigest()[:16]
    return WORK_DIR / f"{key}.work.ohm"


def _connect(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(path, check_same_thread=False, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    return conn


def _check_file(conn: sqlite3.Connection, path: Path) -> None:
    app_id = conn.execute("PRAGMA application_id").fetchone()[0]
    if app_id != schema.APPLICATION_ID:
        raise ApiError(422, "not_a_project", f"{path.name} is not an Ohm project file.")


def _migrate(conn: sqlite3.Connection) -> None:
    version = conn.execute("PRAGMA user_version").fetchone()[0]
    if version > schema.PROJECT_VERSION:
        raise ApiError(422, "newer_project", "This project was made by a newer version of the Curator.")
    for step in schema.PROJECT_MIGRATIONS[version - 1 : schema.PROJECT_VERSION - 1]:
        if isinstance(step, str):
            conn.executescript(step)
            continue
        conn.execute("BEGIN")
        try:
            step(conn)
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise
    conn.execute(f"PRAGMA user_version = {schema.PROJECT_VERSION}")


class Project:
    def __init__(self, path: Path, work_path: Path, conn: sqlite3.Connection, dirty: bool):
        self.path = path
        self.work_path = work_path
        self.conn = conn
        self.dirty = dirty
        self.saved_at: Optional[int] = None if dirty else int(path.stat().st_mtime * 1000) if path.exists() else None
        self.lock = threading.RLock()

    # ── lifecycle ──────────────────────────────────────────────────────────
    @classmethod
    def create(cls, path: Path, name: str) -> "Project":
        path = Path(path)
        work = _work_path(path)
        for p in (work, Path(f"{work}-wal"), Path(f"{work}-shm")):
            p.unlink(missing_ok=True)
        conn = _connect(work)
        conn.execute(f"PRAGMA application_id = {schema.APPLICATION_ID}")
        conn.execute(f"PRAGMA user_version = {schema.PROJECT_VERSION}")
        conn.executescript(schema.PROJECT_SCHEMA)
        meta = {"name": name, "created_at": now_ms(), **schema.DEFAULT_SETTINGS}
        conn.executemany("INSERT INTO meta (key, value) VALUES (?, ?)", [(k, json.dumps(v)) for k, v in meta.items()])
        project = cls(path, work, conn, dirty=True)
        project.save()
        return project

    @classmethod
    def open(cls, path: Path, recover: Optional[bool] = None) -> "Project":
        path = Path(path)
        if not path.exists():
            raise ApiError(404, "not_found", f"{path} does not exist.")
        work = _work_path(path)
        if work.exists() and work.stat().st_mtime > path.stat().st_mtime:
            if recover is None:
                raise ApiError(
                    409, "recovery_available", "There are unsaved changes from last time.",
                    work_saved_at=int(work.stat().st_mtime * 1000),
                )
        else:
            recover = False
        if not recover:
            for p in (work, Path(f"{work}-wal"), Path(f"{work}-shm")):
                p.unlink(missing_ok=True)
            shutil.copyfile(path, work)
        conn = _connect(work)
        try:
            _check_file(conn, path)
            _migrate(conn)
        except Exception:
            conn.close()
            raise
        return cls(path, work, conn, dirty=bool(recover))

    def save(self, path: Optional[Path] = None) -> int:
        target = Path(path) if path else self.path
        tmp = target.with_name(target.name + ".tmp")
        with self.lock:
            tmp.unlink(missing_ok=True)
            self.conn.execute("VACUUM INTO ?", (str(tmp),))
            with open(tmp, "rb+") as f:
                os.fsync(f.fileno())
            os.replace(tmp, target)
            if path and Path(path) != self.path:
                old_work = self.work_path
                self.path = Path(path)
                self.work_path = _work_path(self.path)
                self.conn.close()
                shutil.copyfile(target, self.work_path)
                self.conn = _connect(self.work_path)
                for p in (old_work, Path(f"{old_work}-wal"), Path(f"{old_work}-shm")):
                    p.unlink(missing_ok=True)
            self.dirty = False
            self.saved_at = now_ms()
            return self.saved_at

    def close(self) -> None:
        with self.lock:
            self.conn.close()
            for p in (self.work_path, Path(f"{self.work_path}-wal"), Path(f"{self.work_path}-shm")):
                p.unlink(missing_ok=True)

    # ── queries ────────────────────────────────────────────────────────────
    @contextmanager
    def tx(self) -> Iterator[sqlite3.Connection]:
        """One write transaction. Marks the project dirty when it commits."""
        with self.lock:
            self.conn.execute("BEGIN IMMEDIATE")
            try:
                yield self.conn
            except BaseException:
                self.conn.execute("ROLLBACK")
                raise
            self.conn.execute("COMMIT")
            self.dirty = True

    def all(self, sql: str, params: Any = ()) -> list[dict]:
        with self.lock:
            return [dict(r) for r in self.conn.execute(sql, params).fetchall()]

    def one(self, sql: str, params: Any = ()) -> Optional[dict]:
        with self.lock:
            row = self.conn.execute(sql, params).fetchone()
            return dict(row) if row else None

    def scalar(self, sql: str, params: Any = ()) -> Any:
        with self.lock:
            row = self.conn.execute(sql, params).fetchone()
            return row[0] if row else None

    # ── settings ───────────────────────────────────────────────────────────
    def settings(self) -> dict:
        rows = self.all("SELECT key, value FROM meta")
        values = {r["key"]: json.loads(r["value"]) for r in rows}
        return {**schema.DEFAULT_SETTINGS, **values}

    def setting(self, key: str) -> Any:
        return self.settings()[key]

    def set_settings(self, conn: sqlite3.Connection, values: dict) -> None:
        conn.executemany(
            "INSERT INTO meta (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            [(k, json.dumps(v)) for k, v in values.items()],
        )

    def info(self) -> dict:
        s = self.settings()
        counts = self.one(
            "SELECT (SELECT COUNT(*) FROM messages) AS messages, (SELECT COUNT(*) FROM authors) AS people, "
            "(SELECT COUNT(*) FROM conversations) AS conversations, "
            "(SELECT COUNT(*) FROM conversations WHERE approved_at IS NOT NULL AND archived_at IS NULL) AS approved"
        )
        return {
            "path": str(self.path), "name": s.get("name"), "dirty": self.dirty, "saved_at": self.saved_at,
            "settings": {k: s[k] for k in schema.DEFAULT_SETTINGS}, "counts": counts,
        }


class State:
    project: Optional[Project] = None


state = State()


def require_project() -> Project:
    if state.project is None:
        raise ApiError(409, "no_project", "Open or create a project first.")
    return state.project
