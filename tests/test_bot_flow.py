import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from bot import handle_url, cmd_start
from utils import is_valid_url, is_safe_url

@pytest.mark.asyncio
async def test_cmd_start():
    message = AsyncMock()
    message.from_user.id = 123
    message.answer = AsyncMock()

    with patch('db.db.add_user') as mock_add_user:
        await cmd_start(message)
        mock_add_user.assert_called_with(123)
        message.answer.assert_called()

@pytest.mark.asyncio
async def test_handle_url_invalid():
    message = AsyncMock()
    message.text = "not a url"
    message.from_user.id = 123
    message.answer = AsyncMock()

    await handle_url(message)
    # For invalid URL, it should just return (or answer if we wanted, but current code returns)
    message.answer.assert_not_called()

@pytest.mark.asyncio
async def test_handle_url_blocked_ip():
    message = AsyncMock()
    message.text = "http://127.0.0.1/secret"
    message.from_user.id = 123
    message.answer = AsyncMock()

    await handle_url(message)
    message.answer.assert_called_with("❌ This URL is not allowed (private/local IP or invalid).")

@pytest.mark.asyncio
async def test_full_flow_success():
    message = AsyncMock()
    message.text = "http://example.com/file.txt"
    message.from_user.id = 123

    status_msg = AsyncMock()
    message.answer.return_value = status_msg

    with patch('bot.is_valid_url', return_value=True), \
         patch('bot.is_safe_url', return_value=True), \
         patch('bot.check_rate_limit', return_value=True), \
         patch('db.db.add_download', return_value=1), \
         patch('bot.download_file', new_callable=AsyncMock) as mock_download, \
         patch('db.db.update_download_status') as mock_update_status, \
         patch('bot.FSInputFile') as mock_fs_input, \
         patch('bot.cleanup_file') as mock_cleanup:

        mock_download.return_value = ("downloads/1_file.txt", 1024)

        await handle_url(message)

        assert status_msg.edit_text.call_count >= 2
        message.answer_document.assert_called()
        assert mock_update_status.call_count >= 2
        mock_cleanup.assert_called()
        status_msg.delete.assert_called()
