
import pytest

from db import Database


@pytest.fixture
def test_db(tmp_path):
    db = Database(str(tmp_path / "test.db"))
    yield db


def test_user_creation(test_db):
    assert test_db.add_user(12345) is True
    assert test_db.add_user(12345) is False  # duplicate is ignored

    with test_db.get_connection() as conn:
        users = conn.execute("SELECT * FROM users").fetchall()
    assert len(users) == 1
    assert users[0]["telegram_id"] == 12345
    assert test_db.count_users() == 1


def test_download_record(test_db):
    user_id = 12345
    test_db.add_user(user_id)
    download_id = test_db.add_download(user_id, "http://example.com/file.zip")

    downloads = test_db.get_user_downloads(user_id)
    assert len(downloads) == 1
    assert downloads[0]["url"] == "http://example.com/file.zip"
    assert downloads[0]["status"] == "downloading"

    test_db.update_download_status(download_id, "completed", filename="file.zip", size=1024)

    downloads = test_db.get_user_downloads(user_id)
    assert downloads[0]["status"] == "completed"
    assert downloads[0]["filename"] == "file.zip"
    assert downloads[0]["size"] == 1024
    assert test_db.get_download(download_id)["url"] == "http://example.com/file.zip"


def test_stats_aggregate(test_db):
    test_db.add_user(1)
    test_db.add_user(2)
    first = test_db.add_download(1, "http://example.com/a.zip")
    second = test_db.add_download(2, "http://example.com/b.zip")
    test_db.update_download_status(first, "completed", filename="a.zip", size=2048)
    test_db.update_download_status(second, "failed")

    stats = test_db.stats()
    assert stats["users"] == 2
    assert stats["total"] == 2
    assert stats["completed"] == 1
    assert stats["failed"] == 1
    assert stats["bytes"] == 2048
    assert stats["today"] == 2

    personal = test_db.user_stats(1)
    assert personal == {"total": 1, "completed": 1, "failed": 0, "bytes": 2048}


def test_wal_mode_enabled(test_db):
    with test_db.get_connection() as conn:
        mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
    assert mode.lower() == "wal"


def test_missing_download_returns_none(test_db):
    assert test_db.get_download(999) is None
