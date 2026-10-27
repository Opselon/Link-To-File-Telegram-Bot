"""SQLite adapters for the repository ports.

One small connection per operation (WAL + busy_timeout) keeps the workers
from blocking the handlers, and every public method maps 1:1 to a port.

The ``downloads`` table *is* the queue: as of v3.3 a job is written with
``status='queued'`` and its requested quality before it ever touches the
in-memory asyncio queue, so a restart can rebuild the queue from disk.
"""

from __future__ import annotations

import logging
import sqlite3
from pathlib import Path
from typing import Any, Optional, Sequence

from linktofile.config import DB_PATH
from linktofile.domain.errors import DuplicateJob
from linktofile.domain.models import (
    BUSY_STATUSES,
    DownloadStatus,
    DownloadTicket,
    FailureKind,
    Quality,
    QualityKind,
    RecoveryReport,
    ResumableJob,
)

logger = logging.getLogger(__name__)

_SCHEMA_VERSION = 2

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
    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    quality TEXT,
    attempts INTEGER NOT NULL DEFAULT 0,
    error TEXT,
    error_kind TEXT,
    FOREIGN KEY (user_id) REFERENCES users (telegram_id)
);

CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_downloads_user ON downloads (user_id);
CREATE INDEX IF NOT EXISTS idx_downloads_status ON downloads (status);

-- One live download per (user, url): a double-tapped link must never occupy
-- two queue slots or download twice. Partial index, so history rows are free.
CREATE UNIQUE INDEX IF NOT EXISTS idx_downloads_busy
    ON downloads (user_id, url)
    WHERE status IN ('queued', 'downloading', 'uploading');
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
            self._migrate(conn)
            conn.commit()
            self._wal_ready = True
        finally:
            conn.close()

    def _migrate(self, conn: sqlite3.Connection) -> None:
        """Bring an older database up to the current shape, in place.

        Everything is idempotent: the columns and the dedupe index already
        exist on a fresh database, so the migration is a no-op there. Rows that
        would violate the dedupe index (several live jobs for one link — only
        possible from an older version) keep the newest and fail the rest, so
        the index can be created at all.
        """
        columns = {row["name"] for row in conn.execute("PRAGMA table_info(downloads)")}
        additions = {
            "updated_at": "DATETIME",
            "quality": "TEXT",
            "attempts": "INTEGER NOT NULL DEFAULT 0",
            "error": "TEXT",
            "error_kind": "TEXT",
        }
        for name, declaration in additions.items():
            if name not in columns:
                conn.execute(f"ALTER TABLE downloads ADD COLUMN {name} {declaration}")
                if name == "updated_at":
                    conn.execute("UPDATE downloads SET updated_at = CURRENT_TIMESTAMP WHERE updated_at IS NULL")

        duplicates = conn.execute(
            "SELECT d.id FROM downloads d"
            " JOIN downloads newer"
            "   ON newer.user_id = d.user_id AND newer.url = d.url AND newer.id > d.id"
            f" WHERE d.status IN ({','.join('?' * len(BUSY_STATUSES))})"
            f"   AND newer.status IN ({','.join('?' * len(BUSY_STATUSES))})",
            BUSY_STATUSES + BUSY_STATUSES,
        ).fetchall()
        for row in duplicates:
            conn.execute(
                "UPDATE downloads SET status = ?, error = ?, error_kind = ?,"
                " updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                (
                    DownloadStatus.FAILED.value,
                    "Superseded by a newer attempt of the same link.",
                    FailureKind.DUPLICATE.value,
                    int(row["id"]),
                ),
            )
        if duplicates:
            logger.info("Dedupe migration failed %s superseded job(s)", len(duplicates))

        conn.execute(f"PRAGMA user_version = {_SCHEMA_VERSION}")


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

    def ids(self) -> list[int]:
        """Every known user, oldest first (broadcast fan-out)."""
        with self._db.connect() as conn:
            rows = conn.execute("SELECT telegram_id FROM users ORDER BY id").fetchall()
        return [int(row["telegram_id"]) for row in rows]


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

    def enqueue(self, ticket_id: int, quality: Quality) -> None:
        """Commit a job to the durable queue.

        The partial unique index turns a second live job for the same
        (user, url) into an integrity error, which becomes ``DuplicateJob``.
        Called *before* the in-memory submit so a crash between the two leaves
        the job resumable rather than lost.
        """
        with self._db.connect() as conn:
            try:
                cursor = conn.execute(
                    "UPDATE downloads SET status = ?, quality = ?,"
                    " updated_at = CURRENT_TIMESTAMP"
                    " WHERE id = ? AND status IN ('pending', 'queued')",
                    (
                        DownloadStatus.QUEUED.value,
                        quality.encode(),
                        ticket_id,
                    ),
                )
                conn.commit()
            except sqlite3.IntegrityError as exc:
                raise DuplicateJob(
                    "This link is already queued or downloading."
                ) from exc
            if cursor.rowcount != 1:
                raise DuplicateJob("This link is already queued or downloading.")

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
        quality: Optional[Quality] = None,
        attempts: Optional[int] = None,
        error: Optional[str] = None,
        error_kind: Optional[str] = None,
    ) -> None:
        with self._db.connect() as conn:
            fields = ["status = ?", "updated_at = CURRENT_TIMESTAMP"]
            params: list[Any] = [status.value]
            if filename is not None:
                fields.append("filename = ?")
                params.append(filename)
            if size is not None:
                fields.append("size = ?")
                params.append(size)
            if quality is not None:
                fields.append("quality = ?")
                params.append(quality.encode())
            if attempts is not None:
                fields.append("attempts = ?")
                params.append(max(0, int(attempts)))
            if error is not None:
                fields.append("error = ?")
                params.append(str(error)[:500])
            if error_kind is not None:
                fields.append("error_kind = ?")
                params.append(str(error_kind))
            params.append(ticket_id)
            conn.execute(f"UPDATE downloads SET {', '.join(fields)} WHERE id = ?", params)
            conn.commit()

    def fail(
        self,
        ticket_id: int,
        *,
        kind: FailureKind = FailureKind.UNKNOWN,
        error: str = "",
    ) -> None:
        self.update(
            ticket_id,
            DownloadStatus.FAILED,
            error=error or kind.value,
            error_kind=kind.value,
        )

    def cancel(self, ticket_id: int) -> bool:
        """Cancel a ticket that is not finished yet. Idempotent."""
        with self._db.connect() as conn:
            cursor = conn.execute(
                "UPDATE downloads SET status = ?, error_kind = NULL,"
                " updated_at = CURRENT_TIMESTAMP"
                " WHERE id = ? AND status IN ('pending', 'queued', 'downloading', 'uploading')",
                (DownloadStatus.CANCELLED.value, ticket_id),
            )
            conn.commit()
            return int(cursor.rowcount or 0) == 1

    def recent(self, user_id: int, limit: int = 5) -> Sequence[DownloadTicket]:
        with self._db.connect() as conn:
            rows = conn.execute(
                "SELECT * FROM downloads WHERE user_id = ? ORDER BY id DESC LIMIT ?",
                (user_id, limit),
            ).fetchall()
        return [_to_ticket(row) for row in rows]

    def active_duplicate(self, user_id: int, url: str) -> Optional[DownloadTicket]:
        with self._db.connect() as conn:
            placeholders = ",".join("?" * len(BUSY_STATUSES))
            row = conn.execute(
                f"SELECT * FROM downloads WHERE user_id = ? AND url = ?"
                f" AND status IN ({placeholders}) ORDER BY id DESC LIMIT 1",
                (user_id, url, *BUSY_STATUSES),
            ).fetchone()
        return _to_ticket(row) if row else None

    def owner(self, ticket_id: int) -> Optional[int]:
        with self._db.connect() as conn:
            row = conn.execute("SELECT user_id FROM downloads WHERE id = ?", (ticket_id,)).fetchone()
        return int(row["user_id"]) if row else None

    # --- durable queue -------------------------------------------------------
    def recover_after_restart(self) -> RecoveryReport:
        """Rebuild a consistent queue picture after a process died.

        * ``queued``   → returned for re-submission (quality is on the row).
        * ``downloading`` / ``uploading`` → failed as ``interrupted``: the file
          lived in a temp dir that no longer exists, so resuming is impossible;
          the honest state is "failed by restart", not a forever-running job.
        * ``pending``  → failed as ``abandoned``: no quality was ever picked.
        """
        report = RecoveryReport()
        with self._db.connect() as conn:
            cursor = conn.execute(
                "UPDATE downloads SET status = ?, error = ?, error_kind = ?,"
                " updated_at = CURRENT_TIMESTAMP"
                " WHERE status IN ('downloading', 'uploading')",
                (
                    DownloadStatus.FAILED.value,
                    "The bot restarted while this was running. Tap Try again.",
                    FailureKind.INTERRUPTED.value,
                ),
            )
            report.interrupted = int(cursor.rowcount or 0)

            cursor = conn.execute(
                "UPDATE downloads SET status = ?, error = ?, error_kind = ?,"
                " updated_at = CURRENT_TIMESTAMP WHERE status = 'pending'",
                (
                    DownloadStatus.FAILED.value,
                    "Never finished picking a quality.",
                    FailureKind.ABANDONED.value,
                ),
            )
            report.abandoned = int(cursor.rowcount or 0)

            rows = conn.execute(
                "SELECT id, user_id, url, quality, attempts FROM downloads"
                " WHERE status = 'queued' ORDER BY id"
            ).fetchall()
            conn.commit()

        for row in rows:
            quality = Quality.decode(row["quality"])
            if quality is None:
                # A queued row without a parsable quality cannot be run safely;
                # fail it instead of guessing.
                self.fail(row["id"], kind=FailureKind.INTERRUPTED, error="Queued quality was lost.")
                continue
            report.resumable.append(
                ResumableJob(
                    ticket_id=int(row["id"]),
                    user_id=int(row["user_id"]),
                    url=str(row["url"]),
                    quality=quality,
                    attempts=int(row["attempts"] or 0),
                )
            )
        return report

    def reap_stale(self) -> int:
        """Backwards-compatible alias for the old boot sweep."""
        report = self.recover_after_restart()
        return report.interrupted + report.abandoned

    def counts_by_status(self) -> dict[str, int]:
        with self._db.connect() as conn:
            rows = conn.execute(
                "SELECT status, COUNT(*) AS n FROM downloads GROUP BY status"
            ).fetchall()
        return {str(row["status"]): int(row["n"]) for row in rows}

    def queue_contents(self, limit: int = 12) -> Sequence[DownloadTicket]:
        """Live jobs for the admin panel, oldest first."""
        with self._db.connect() as conn:
            placeholders = ",".join("?" * len(BUSY_STATUSES))
            rows = conn.execute(
                f"SELECT * FROM downloads WHERE status IN ({placeholders})"
                f" ORDER BY id LIMIT ?",
                (*BUSY_STATUSES, limit),
            ).fetchall()
        return [_to_ticket(row) for row in rows]

    def recent_failures(self, limit: int = 8) -> Sequence[DownloadTicket]:
        with self._db.connect() as conn:
            rows = conn.execute(
                "SELECT * FROM downloads WHERE status = 'failed'"
                " ORDER BY updated_at DESC, id DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [_to_ticket(row) for row in rows]


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
            conn.commit()


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
                " COALESCE(SUM(CASE WHEN status = 'queued' THEN 1 ELSE 0 END), 0) AS queued,"
                " COALESCE(SUM(CASE WHEN status = 'downloading' THEN 1 ELSE 0 END), 0) AS downloading,"
                " COALESCE(SUM(CASE WHEN status = 'uploading' THEN 1 ELSE 0 END), 0) AS uploading,"
                " COALESCE(SUM(CASE WHEN error_kind IS NOT NULL AND status = 'failed'"
                "            THEN 1 ELSE 0 END), 0) AS errors,"
                " COALESCE(SUM(CASE WHEN date(created_at) = date('now') THEN 1 ELSE 0 END), 0) AS today"
                " FROM downloads"
            ).fetchone()
        return {key: int(row[key] or 0) for key in row.keys()}


def _to_ticket(row: sqlite3.Row) -> DownloadTicket:
    status = DownloadStatus(row["status"] or "pending")
    quality_raw = row["quality"] if "quality" in row.keys() else None
    attempts_raw = row["attempts"] if "attempts" in row.keys() else 0
    error_raw = row["error"] if "error" in row.keys() else None
    kind_raw = row["error_kind"] if "error_kind" in row.keys() else None
    return DownloadTicket(
        id=int(row["id"]),
        user_id=int(row["user_id"]),
        url=str(row["url"]),
        status=status,
        filename=row["filename"],
        size=int(row["size"]) if row["size"] else None,
        quality=Quality.decode(quality_raw),
        attempts=int(attempts_raw or 0),
        error=error_raw,
        error_kind=kind_raw,
        created_at=str(row["created_at"]) if "created_at" in row.keys() and row["created_at"] else None,
    )


__all__ = [
    "Database",
    "SqliteUsers",
    "SqliteTickets",
    "SqliteSettings",
    "SqliteStats",
    "Quality",
    "QualityKind",
]
