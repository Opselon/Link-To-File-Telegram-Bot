"""SQLite adapters behave like the ports they implement."""

from linktofile.domain.models import DownloadStatus
from linktofile.infrastructure.persistence.sqlite import (
    SqliteSettings,
    SqliteStats,
    SqliteTickets,
    SqliteUsers,
)


def test_users_are_registered_once(database):
    users = SqliteUsers(database)
    assert users.register(12345) is True
    assert users.register(12345) is False
    assert users.count() == 1


def test_ticket_round_trip(database):
    users, tickets = SqliteUsers(database), SqliteTickets(database)
    users.register(7)
    ticket = tickets.create(7, "https://example.com/a.zip")

    loaded = tickets.get(ticket.id)
    assert loaded is not None
    assert loaded.url == "https://example.com/a.zip"
    assert loaded.status is DownloadStatus.PENDING
    assert tickets.owner(ticket.id) == 7


def test_ticket_updates_status_and_metadata(database):
    users, tickets = SqliteUsers(database), SqliteTickets(database)
    users.register(7)
    ticket = tickets.create(7, "https://example.com/a.zip")

    tickets.update(ticket.id, DownloadStatus.COMPLETED, filename="a.zip", size=2048)
    loaded = tickets.get(ticket.id)
    assert loaded.status is DownloadStatus.COMPLETED
    assert loaded.filename == "a.zip"
    assert loaded.size == 2048


def test_recent_is_scoped_to_the_user(database):
    users, tickets = SqliteUsers(database), SqliteTickets(database)
    users.register(1)
    users.register(2)
    tickets.create(1, "https://example.com/one")
    tickets.create(2, "https://example.com/two")

    assert [t.url for t in tickets.recent(1)] == ["https://example.com/one"]
    assert tickets.get(999_999) is None


def test_settings_are_persisted(database):
    settings = SqliteSettings(database)
    assert settings.get("force_join.enabled", "0") == "0"
    settings.set("force_join.enabled", "1")
    assert settings.get("force_join.enabled") == "1"
    settings.set("force_join.enabled", "0")
    assert settings.get("force_join.enabled") == "0"


def test_stats_count_delivered_bytes(database):
    users = SqliteUsers(database)
    tickets = SqliteTickets(database)
    stats = SqliteStats(database)

    users.register(3)
    done = tickets.create(3, "https://example.com/a")
    tickets.update(done.id, DownloadStatus.COMPLETED, size=1024)
    failed = tickets.create(3, "https://example.com/b")
    tickets.update(failed.id, DownloadStatus.FAILED)

    mine = stats.user(3)
    assert mine["total"] == 2
    assert mine["completed"] == 1
    assert mine["failed"] == 1

    everything = stats.global_()
    assert everything["users"] == 1
    assert everything["total"] == 2
    assert everything["bytes"] >= 1024


def test_legacy_schema_migration_with_missing_columns(tmp_path):
    import sqlite3
    from linktofile.infrastructure.persistence.sqlite import Database

    db_path = tmp_path / "legacy.db"
    conn = sqlite3.connect(db_path)
    # Simulate an older v2 schema without updated_at, quality, attempts, error
    conn.executescript("""
        CREATE TABLE users (user_id INTEGER PRIMARY KEY);
        CREATE TABLE downloads (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            url TEXT NOT NULL,
            filename TEXT,
            size INTEGER,
            status TEXT DEFAULT 'pending',
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP
        );
        INSERT INTO downloads (user_id, url) VALUES (1, 'https://example.com/legacy');
    """)
    conn.commit()
    conn.close()

    # Opening Database triggers _migrate
    db = Database(db_path)
    tickets = SqliteTickets(db)
    item = tickets.get(1)
    assert item is not None
    assert item.url == "https://example.com/legacy"

    with db.connect() as check_conn:
        row = check_conn.execute("SELECT updated_at FROM downloads WHERE id = 1").fetchone()
        assert row is not None
        assert row[0] is not None


