from unittest.mock import patch

from linktofile.infrastructure.system.updater import Updater, _version_tuple


def test_version_tuple_ordering():
    assert _version_tuple("2.0.0") > _version_tuple("1.9.9")
    assert _version_tuple("v2.1") == (2, 1)


def test_backup_creation(tmp_path):
    updater = Updater()
    updater.base_dir = tmp_path
    (tmp_path / ".env").write_text("TOKEN=1")
    (tmp_path / "bot_database.db").write_text("db")

    with patch("linktofile.infrastructure.system.updater.shutil.copy2") as copy:
        backup_path = updater.backup_config()

    assert backup_path.exists()
    assert "backup" in str(backup_path)
    assert copy.call_count == 2


def test_rollback_mechanism(tmp_path):
    updater = Updater()
    updater.base_dir = tmp_path
    backup_dir = tmp_path / "backup" / "last"
    backup_dir.mkdir(parents=True)
    (backup_dir / ".env").write_text("OLD_TOKEN=123")

    with patch.object(Updater, "latest_release", return_value="9.9.9"), \
         patch.object(Updater, "pull_latest", return_value=False), \
         patch.object(Updater, "backup_config", return_value=backup_dir), \
         patch("linktofile.infrastructure.system.updater.shutil.copy2") as copy:
        ok = updater.run_update()

    assert ok is False
    copy.assert_any_call(backup_dir / ".env", tmp_path / ".env")


def test_up_to_date_short_circuits(tmp_path):
    from linktofile.infrastructure.system.version import VERSION

    updater = Updater()
    updater.base_dir = tmp_path
    with patch.object(Updater, "latest_release", return_value=VERSION), \
         patch.object(Updater, "pull_latest") as pull:
        assert updater.run_update() is True
    pull.assert_not_called()


def test_non_git_install_is_explained(tmp_path):
    updater = Updater()
    updater.base_dir = tmp_path  # no .git here
    assert updater.is_git_checkout() is False
    assert updater.pull_latest() is False
