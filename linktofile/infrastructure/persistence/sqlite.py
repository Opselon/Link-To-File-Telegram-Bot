"""SQLite adapters for the repository ports.

One small connection per operation (WAL + busy_timeout) keeps the workers
from blocking the handlers, and every public method maps 1:1 to a port.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any, Optional, Sequence

from linktofile.config import DB_PATH
from linktofile.domain.models import DownloadStatus, DownloadTicket, Quality, QualityKind

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

CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_downloads_user ON downloads (user_id);
CREATE INDEX IF NOT EXISTS idx_downloads_status ON downloads (status);
"""


class Database:
    """Thin connection factory; the schema lives here and nowhere else."""

    def __init__(self, db_path: str | Path | None = None) -> None:
        self.db_path = str(db_path or DB_PATH)
        self._wal_ready = False
        self.init_db()

    def connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, timeout=20)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA busy_timeout = 20000")
        conn.execute("PRAGMA foreign_keys = ON")
        if self._wal_ready:
            conn.execute("PRAGMA synchronous = NORMAL")
        return conn

    def init_db(self) -> None:
        conn = self.connect()
        try:
            conn.execute("PRAGMA journal_mode = WAL")
            conn.execute("PRAGMA synchronous = NORMAL")
            conn.executescript(_SCHEMA)
            conn.commit()
            self._wal_ready = True
        finally:
            conn.close()


class SqliteUsers:
    def __init__(self, database: Database) -> None:
        self._db = database

    def register(self, telegram_id: int) -> bool:
        with self._db.connect() as conn:
            cursor = conn.execute(
                "INSERT OR IGNORE INTO users (telegram_id) VALUES (?)", (telegram_id,)
            )
            return cursor.rowcount == 1

    def count(self) -> int:
        with self._db.connect() as conn:
            row = conn.execute("SELECT COUNT(*) AS n FROM users").fetchone()
            return int(row["n"]) if row else 0


class SqliteTickets:
    def __init__(self, database: Database) -> None:
        self._db = database

    def create(self, user_id: int, url: str) -> DownloadTicket:
        with self._db.connect() as conn:
            cursor = conn.execute(
                "INSERT INTO downloads (user_id, url, status) VALUES (?, ?, ?)",
                (user_id, url, DownloadStatus.PENDING.value),
            )
            return DownloadTicket(id=int(cursor.lastrowid or 0), user_id=user_id, url=url)

    def get(self, ticket_id: int) -> Optional[DownloadTicket]:
        with self._db.connect() as conn:
            row = conn.execute("SELECT * FROM downloads WHERE id = ?", (ticket_id,)).fetchone()
        return _to_ticket(row) if row else None

    def update(
        self,
        ticket_id: int,
        status: DownloadStatus,
        *,
        filename: Optional[str] = None,
        size: Optional[int] = None,
    ) -> None:
        with self._db.connect() as conn:
            fields = ["status = ?"]
            params: list[Any] = [status.value]
            if filename is not None:
                fields.append("filename = ?")
                params.append(filename)
            if size is not None:
                fields.append("size = ?")
                params.append(size)
            params.append(ticket_id)
            conn.execute(f"UPDATE downloads SET {', '.join(fields)} WHERE id = ?", params)

    def recent(self, user_id: int, limit: int = 5) -> Sequence[DownloadTicket]:
        with self._db.connect() as conn:
            rows = conn.execute(
                "SELECT * FROM downloads WHERE user_id = ? ORDER BY id DESC LIMIT ?",
                (user_id, limit),
            ).fetchall()
        return [_to_ticket(row) for row in rows]

    def owner(self, ticket_id: int) -> Optional[int]:
        with self._db.connect() as conn:
            row = conn.execute("SELECT user_id FROM downloads WHERE id = ?", (ticket_id,)).fetchone()
        return int(row["user_id"]) if row else None


class SqliteSettings:
    def __init__(self, database: Database) -> None:
        self._db = database

    def get(self, key: str, default: str = "") -> str:
        with self._db.connect() as conn:
            row = conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
        return str(row["value"]) if row else default

    def set(self, key: str, value: str) -> None:
        with self._db.connect() as conn:
            conn.execute(
                "INSERT INTO settings (key, value) VALUES (?, ?)"
                " ON CONFLICT(key) DO UPDATE SET value = excluded.value,"
                " updated_at = CURRENT_TIMESTAMP",
                (key, value),
            )


class SqliteStats:
    def __init__(self, database: Database) -> None:
        self._db = database

    def user(self, user_id: int) -> dict[str, int]:
        with self._db.connect() as conn:
            row = conn.execute(
                "SELECT COUNT(*) AS total,"
                " COALESCE(SUM(CASE WHEN status = 'completed' THEN 1 ELSE 0 END), 0) AS completed,"
                " COALESCE(SUM(CASE WHEN status = 'failed' THEN 1 ELSE 0 END), 0) AS failed,"
                " COALESCE(SUM(CASE WHEN status = 'completed' THEN size ELSE 0 END), 0) AS bytes"
                " FROM downloads WHERE user_id = ?",
                (user_id,),
            ).fetchone()
        return {key: int(row[key] or 0) for key in ("total", "completed", "failed", "bytes")}

    def global_(self) -> dict[str, int]:
        with self._db.connect() as conn:
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
        return {key: int(row[key] or 0) for key in row.keys()}


def _to_ticket(row: sqlite3.Row) -> DownloadTicket:
    status = DownloadStatus(row["status"] or "pending")
    return DownloadTicket(
        id=int(row["id"]),
        user_id=int(row["user_id"]),
        url=str(row["url"]),
        status=status,
        filename=row["filename"],
        size=int(row["size"]) if row["size"] else None,
    )


__all__ = ["Database", "SqliteUsers", "SqliteTickets", "SqliteSettings", "SqliteStats", "Quality", "QualityKind"]
