import os
from unittest.mock import patch, MagicMock
from cli.wizard import run_wizard
from pathlib import Path

def test_wizard_flow():
    # Mocking user inputs: Token, Admin ID, Max Size, Advanced (n)
    inputs = ["123456:ABC-DEF", "987654321", "200", "n"]

    with patch("rich.prompt.Prompt.ask", side_effect=inputs), \
         patch("rich.prompt.IntPrompt.ask", side_effect=[987654321, 200]), \
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

            mock_db_init.assert_called_once()
        finally:
            if os.path.exists(".env"):
                os.remove(".env")
            if os.path.exists(".env.bak"):
                os.rename(".env.bak", ".env")

def test_wizard_invalid_token():
    # Mocking user inputs: Invalid Token, Valid Token, Advanced (n)
    # Admin ID and Max Size are IntPrompt.ask
    inputs = ["invalid_token", "123456:ABC-DEF", "n"]

    # We need to track calls to Prompt.ask
    with patch("rich.prompt.Prompt.ask", side_effect=inputs) as mock_prompt, \
         patch("rich.prompt.IntPrompt.ask", side_effect=[987654321, 200]), \
         patch("db.db.init_db"):

        # Mock rprint to avoid actual output
        with patch("cli.wizard.rprint"):
            if os.path.exists(".env"):
                os.rename(".env", ".env.bak")
            try:
                run_wizard()
                # 2 for token (one invalid, one valid) + 1 for advanced = 3
                assert mock_prompt.call_count == 3
            finally:
                if os.path.exists(".env"):
                    os.remove(".env")
                if os.path.exists(".env.bak"):
                    os.rename(".env.bak", ".env")
