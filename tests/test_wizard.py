from unittest.mock import patch

from cli.wizard import run_wizard

TOKEN = "123456:ABCDEFGHIJKLMNOPQRSTUVWXYZ012345"


def test_wizard_writes_env_without_prompts(tmp_path):
    env_file = tmp_path / ".env"
    saved = run_wizard(
        bot_token=TOKEN,
        admin_id=987654321,
        max_size=200,
        timeout=600,
        api_url="http://1.2.3.4:8081",
        yes=True,
        verify=False,
        env_path=env_file,
    )

    assert saved is True
    content = env_file.read_text()
    assert f"BOT_TOKEN={TOKEN}" in content
    assert "ADMIN_ID=987654321" in content
    assert "MAX_FILE_SIZE_MB=200" in content
    assert "DOWNLOAD_TIMEOUT=600" in content
    assert "TELEGRAM_API_URL=http://1.2.3.4:8081" in content
    assert oct(env_file.stat().st_mode)[-3:] == "600"


def test_wizard_reprompts_on_bad_token(tmp_path):
    env_file = tmp_path / ".env"
    with patch("rich.prompt.Prompt.ask", side_effect=["invalid_token", TOKEN]) as prompt:
        run_wizard(
            bot_token="",
            admin_id=1,
            max_size=50,
            timeout=300,
            api_url="",
            yes=True,
            verify=False,
            env_path=env_file,
        )
    assert prompt.call_count == 2
    assert env_file.read_text().startswith("#")


def test_wizard_clamps_max_size(tmp_path):
    env_file = tmp_path / ".env"
    run_wizard(
        bot_token=TOKEN,
        admin_id=1,
        max_size=99_999,
        timeout=60,
        api_url="",
        yes=True,
        verify=False,
        env_path=env_file,
    )
    assert "MAX_FILE_SIZE_MB=2000" in env_file.read_text()


def test_wizard_respects_declined_save(tmp_path):
    env_file = tmp_path / ".env"
    with patch("rich.prompt.Confirm.ask", return_value=False):
        saved = run_wizard(
            bot_token=TOKEN,
            admin_id=1,
            max_size=50,
            timeout=300,
            api_url="",
            yes=False,
            verify=False,
            env_path=env_file,
        )
    assert saved is False
    assert not env_file.exists()
