import pytest
import os
from db import Database

@pytest.fixture
def test_db():
    db_path = "test_bot_database.db"
    db = Database(db_path)
    yield db
    if os.path.exists(db_path):
        os.remove(db_path)

def test_user_creation(test_db):
    test_db.add_user(12345)
    test_db.add_user(12345)  # Should handle duplicate with INSERT OR IGNORE

    with test_db.get_connection() as conn:
        users = conn.execute("SELECT * FROM users").fetchall()
        assert len(users) == 1
        assert users[0]['telegram_id'] == 12345

def test_download_record(test_db):
    user_id = 12345
    test_db.add_user(user_id)
    download_id = test_db.add_download(user_id, "http://example.com/file.zip")

    downloads = test_db.get_user_downloads(user_id)
    assert len(downloads) == 1
    assert downloads[0]['url'] == "http://example.com/file.zip"
    assert downloads[0]['status'] == "downloading"

    test_db.update_download_status(download_id, "completed", filename="file.zip", size=1024)

    downloads = test_db.get_user_downloads(user_id)
    assert downloads[0]['status'] == "completed"
    assert downloads[0]['filename'] == "file.zip"
    assert downloads[0]['size'] == 1024
