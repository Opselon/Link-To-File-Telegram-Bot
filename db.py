"""SQLite persistence: users, download history and aggregate stats."""

from __future__ import annotations

import sqlite3
from typing import Any, Dict, List, Optional

from config import DB_PATH

_SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    telegram_id INTEGER UNIQUE NOT NULL,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS downloads (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    url TEXT NOT NULL,
    filename TEXT,
    size INTEGER,
    status TEXT DEFAULT 'pending',
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (user_id) REFERENCES users (telegram_id)
);

CREATE INDEX IF NOT EXISTS idx_downloads_user ON downloads (user_id);
CREATE INDEX IF NOT EXISTS idx_downloads_status ON downloads (status);
"""


class Database:
    def __init__(self, db_path: str | None = None):
        self.db_path = str(db_path or DB_PATH)
        self._wal_ready = False
        self.init_db()

    def get_connection(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, timeout=20)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA busy_timeout = 20000")
        conn.execute("PRAGMA foreign_keys = ON")
        if self._wal_ready:
            # Cheap per-connection tuning; journal_mode is persistent.
            conn.execute("PRAGMA synchronous = NORMAL")
        return conn

    def init_db(self) -> None:
        conn = self.get_connection()
        try:
            # WAL lets the download workers write while handlers read.
            conn.execute("PRAGMA journal_mode = WAL")
            conn.execute("PRAGMA synchronous = NORMAL")
            conn.executescript(_SCHEMA)
            conn.commit()
            self._wal_ready = True
        finally:
            conn.close()

    # --- users --------------------------------------------------------------
    def add_user(self, telegram_id: int) -> bool:
        """Register a user. Returns True when they are new."""
        conn = self.get_connection()
        try:
            cursor = conn.execute(
                "INSERT OR IGNORE INTO users (telegram_id) VALUES (?)",
                (telegram_id,),
            )
            conn.commit()
            return cursor.rowcount == 1
        finally:
            conn.close()

    def count_users(self) -> int:
        conn = self.get_connection()
        try:
            row = conn.execute("SELECT COUNT(*) AS n FROM users").fetchone()
            return int(row["n"]) if row else 0
        finally:
            conn.close()

    # --- downloads ----------------------------------------------------------
    def add_download(
        self,
        user_id: int,
        url: str,
        filename: Optional[str] = None,
        size: Optional[int] = None,
    ) -> int:
        conn = self.get_connection()
        try:
            cursor = conn.execute(
                "INSERT INTO downloads (user_id, url, filename, size, status)"
                " VALUES (?, ?, ?, ?, ?)",
                (user_id, url, filename, size, "downloading"),
            )
            conn.commit()
            return int(cursor.lastrowid or 0)
        finally:
            conn.close()

    def update_download_status(
        self,
        download_id: int,
        status: str,
        filename: Optional[str] = None,
        size: Optional[int] = None,
    ) -> None:
        conn = self.get_connection()
        try:
            if filename is not None and size is not None:
                conn.execute(
                    "UPDATE downloads SET status = ?, filename = ?, size = ? WHERE id = ?",
                    (status, filename, size, download_id),
                )
            else:
                conn.execute(
                    "UPDATE downloads SET status = ? WHERE id = ?",
                    (status, download_id),
                )
            conn.commit()
        finally:
            conn.close()

    def get_download(self, download_id: int) -> Optional[Dict[str, Any]]:
        conn = self.get_connection()
        try:
            row = conn.execute(
                "SELECT * FROM downloads WHERE id = ?", (download_id,)
            ).fetchone()
            return dict(row) if row else None
        finally:
            conn.close()

    def get_user_downloads(
        self, user_id: int, limit: int = 100
    ) -> List[Dict[str, Any]]:
        conn = self.get_connection()
        try:
            rows = conn.execute(
                "SELECT * FROM downloads WHERE user_id = ? ORDER BY id DESC LIMIT ?",
                (user_id, limit),
            ).fetchall()
            return [dict(row) for row in rows]
        finally:
            conn.close()

    def user_stats(self, user_id: int) -> Dict[str, int]:
        conn = self.get_connection()
        try:
            row = conn.execute(
                "SELECT COUNT(*) AS total,"
                " COALESCE(SUM(CASE WHEN status = 'completed' THEN 1 ELSE 0 END), 0) AS completed,"
                " COALESCE(SUM(CASE WHEN status = 'failed' THEN 1 ELSE 0 END), 0) AS failed,"
                " COALESCE(SUM(CASE WHEN status = 'completed' THEN size ELSE 0 END), 0) AS bytes"
                " FROM downloads WHERE user_id = ?",
                (user_id,),
            ).fetchone()
            return {key: int(row[key] or 0) for key in ("total", "completed", "failed", "bytes")}
        finally:
            conn.close()

    def stats(self) -> Dict[str, int]:
        conn = self.get_connection()
        try:
            row = conn.execute(
                "SELECT"
                " (SELECT COUNT(*) FROM users) AS users,"
                " COUNT(*) AS total,"
                " COALESCE(SUM(CASE WHEN status = 'completed' THEN 1 ELSE 0 END), 0) AS completed,"
                " COALESCE(SUM(CASE WHEN status = 'failed' THEN 1 ELSE 0 END), 0) AS failed,"
                " COALESCE(SUM(CASE WHEN status = 'completed' THEN size ELSE 0 END), 0) AS bytes,"
                " COALESCE(SUM(CASE WHEN date(created_at) = date('now') THEN 1 ELSE 0 END), 0) AS today"
                " FROM downloads"
            ).fetchone()
            result = {key: int(row[key] or 0) for key in row.keys()}
            result["queued"] = 0
            return result
        finally:
            conn.close()


db = Database()
