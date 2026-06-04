import os
import shutil
from pathlib import Path
from unittest.mock import patch, MagicMock
from core.updater import Updater

def test_backup_creation():
    updater = Updater()
    # Create dummy config files
    with open(".env.test", "w") as f: f.write("test=1")
    with open("bot_database.db.test", "w") as f: f.write("db")

    # Monkeypatch the base_dir and files to backup
    updater.base_dir = Path(".")

    with patch("core.updater.shutil.copy2") as mock_copy:
        backup_path = updater.backup_config()
        assert backup_path.exists()
        assert "backup" in str(backup_path)

        # Cleanup
        os.remove(".env.test")
        os.remove("bot_database.db.test")
        shutil.rmtree("backup")

def test_rollback_mechanism():
    updater = Updater()
    updater.base_dir = Path("./test_env_rollback")
    updater.base_dir.mkdir(exist_ok=True)

    backup_dir = updater.base_dir / "backup/last"
    backup_dir.mkdir(parents=True, exist_ok=True)

    with open(backup_dir / ".env", "w") as f: f.write("OLD_TOKEN=123")

    # Mock pull_latest to fail
    with patch.object(Updater, 'pull_latest', return_value=False), \
         patch.object(Updater, 'backup_config', return_value=backup_dir), \
         patch("core.updater.shutil.copy2") as mock_copy:

        updater.run_update()

        # Verify copy2 was called to restore from backup
        mock_copy.assert_any_call(backup_dir / ".env", updater.base_dir / ".env")

    # Cleanup
    shutil.rmtree(updater.base_dir)
