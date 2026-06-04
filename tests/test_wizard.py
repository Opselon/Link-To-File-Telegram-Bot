import os
from unittest.mock import patch, MagicMock
from cli.wizard import run_wizard
from pathlib import Path

def test_wizard_flow():
    # Enhanced wizard has more prompts
    # 1. Bot Token: 123456:ABC-DEF
    # 2. Admin ID: 987654321
    # 3. Max Size: 200
    # 4. Timeout: 600
    # 5. Configure API (Confirm): True (or y)
    # 6. API Choice: ip_port
    # 7. IP: 1.2.3.4
    # 8. Port: 8081
    # 9. Save (Confirm): True (or y)

    prompt_side_effects = ["123456:ABC-DEF", "ip_port", "1.2.3.4"]
    int_prompt_side_effects = [987654321, 200, 600, 8081]
    confirm_side_effects = [True, True]

    with patch("rich.prompt.Prompt.ask", side_effect=prompt_side_effects), \
         patch("rich.prompt.IntPrompt.ask", side_effect=int_prompt_side_effects), \
         patch("rich.prompt.Confirm.ask", side_effect=confirm_side_effects), \
         patch("db.db.init_db") as mock_db_init:

        # Ensure .env doesn't exist before test
        if os.path.exists(".env"):
            os.rename(".env", ".env.bak")

        try:
            run_wizard()

            assert os.path.exists(".env")
            with open(".env", "r") as f:
                content = f.read()
                assert "BOT_TOKEN=123456:ABC-DEF" in content
                assert "ADMIN_ID=987654321" in content
                assert "MAX_FILE_SIZE_MB=200" in content
                assert "DOWNLOAD_TIMEOUT=600" in content
                assert "TELEGRAM_API_URL=http://1.2.3.4:8081" in content

            mock_db_init.assert_called_once()
        finally:
            if os.path.exists(".env"):
                os.remove(".env")
            if os.path.exists(".env.bak"):
                os.rename(".env.bak", ".env")

def test_wizard_invalid_token():
    # 1. Bot Token: invalid_token
    # 2. Bot Token: 123456:ABC-DEF
    # 3. Admin ID: 987654321
    # 4. Max Size: 200
    # 5. Timeout: 600
    # 6. Configure API (Confirm): False
    # 7. Save (Confirm): True

    prompt_side_effects = ["invalid_token", "123456:ABC-DEF"]
    int_prompt_side_effects = [987654321, 200, 600]
    confirm_side_effects = [False, True]

    # We need to track calls to Prompt.ask
    with patch("rich.prompt.Prompt.ask", side_effect=prompt_side_effects) as mock_prompt, \
         patch("rich.prompt.IntPrompt.ask", side_effect=int_prompt_side_effects), \
         patch("rich.prompt.Confirm.ask", side_effect=confirm_side_effects), \
         patch("db.db.init_db"):

        # Mock rprint to avoid actual output
        with patch("cli.wizard.rprint"):
            if os.path.exists(".env"):
                os.rename(".env", ".env.bak")
            try:
                run_wizard()
                # 2 for token
                assert mock_prompt.call_count == 2
            finally:
                if os.path.exists(".env"):
                    os.remove(".env")
                if os.path.exists(".env.bak"):
                    os.rename(".env.bak", ".env")
