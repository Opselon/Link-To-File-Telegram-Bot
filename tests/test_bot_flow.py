from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from bot import cmd_start, handle_url, send_file
from downloader import DownloadError, DownloadResult
from ui import blocked_url_text


def _user(user_id: int = 123):
    return SimpleNamespace(id=user_id, first_name="Test", username="tester")


def _message(text: str, user_id: int = 123):
    message = AsyncMock()
    message.text = text
    message.from_user = _user(user_id)
    message.chat = SimpleNamespace(id=user_id)
    message.answer = AsyncMock()
    return message


async def _run_now(coro_func, *args, **kwargs):
    await coro_func(*args, **kwargs)


@pytest.mark.asyncio
async def test_cmd_start_registers_and_answers():
    message = _message("/start")
    with patch("bot.db.add_user") as add_user:
        await cmd_start(message)
        add_user.assert_called_once_with(123)
    message.answer.assert_called_once()
    assert message.answer.call_args.kwargs["reply_markup"] is not None


@pytest.mark.asyncio
async def test_handle_url_ignores_plain_text():
    message = _message("hello there")
    await handle_url(message)
    message.answer.assert_not_called()


@pytest.mark.asyncio
async def test_handle_url_blocks_private_ip():
    message = _message("http://127.0.0.1/secret")
    await handle_url(message)
    message.answer.assert_awaited_once_with(blocked_url_text(), parse_mode="HTML")


@pytest.mark.asyncio
async def test_handle_url_shows_quality_menu_for_youtube():
    message = _message("https://www.youtube.com/watch?v=dQw4w9WgXcQ")
    with patch("bot.db.add_user"), patch("bot.db.add_download", return_value=9):
        await handle_url(message)
    kwargs = message.answer.call_args.kwargs
    callbacks = [b.callback_data for row in kwargs["reply_markup"].inline_keyboard for b in row]
    assert any(cb.startswith("q:mp4:") for cb in callbacks)


@pytest.mark.asyncio
async def test_handle_url_runs_a_direct_download(tmp_path):
    message = _message("https://example.com/file.txt")
    status = AsyncMock()
    message.answer.return_value = status
    output = tmp_path / "1_file.txt"
    output.write_bytes(b"x" * 16)
    result = DownloadResult(path=str(output), size=16, title="file.txt")

    with patch("bot.db.add_user"), patch("bot.db.add_download", return_value=1), \
         patch("bot.download_file", new=AsyncMock(return_value=result)), \
         patch("bot.send_file", new=AsyncMock()) as send, \
         patch("bot.download_queue.add_task", side_effect=_run_now), \
         patch("bot.db.update_download_status") as update, \
         patch("bot.cleanup_file") as cleanup:
        await handle_url(message)

    send.assert_awaited_once()
    status.delete.assert_awaited_once()
    cleanup.assert_called_once()
    statuses = [call.args[1] for call in update.call_args_list]
    assert "uploading" in statuses and "completed" in statuses


@pytest.mark.asyncio
async def test_handle_url_reports_rate_limit():
    message = _message("https://example.com/file.txt")
    with patch("bot.rate_limit_remaining", return_value=4):
        await handle_url(message)
    assert "try again" in message.answer.call_args.args[0]


@pytest.mark.asyncio
async def test_failed_download_offers_retry():
    message = _message("https://example.com/file.txt")
    status = AsyncMock()
    message.answer.return_value = status

    with patch("bot.db.add_user"), patch("bot.db.add_download", return_value=2), \
         patch("bot.download_file", new=AsyncMock(side_effect=DownloadError("nope"))), \
         patch("bot.download_queue.add_task", side_effect=_run_now):
        await handle_url(message)

    assert "Couldn't finish" in status.edit_text.call_args.args[0]
    assert status.edit_text.call_args.kwargs["reply_markup"] is not None


@pytest.mark.asyncio
async def test_send_file_picks_the_right_telegram_method(tmp_path):
    for name, method in (
        ("clip.mp4", "answer_video"),
        ("song.mp3", "answer_audio"),
        ("archive.zip", "answer_document"),
    ):
        path = tmp_path / name
        path.write_bytes(b"data")
        message = AsyncMock()
        await send_file(message, str(path), "caption")
        getattr(message, method).assert_awaited_once()


@pytest.mark.asyncio
async def test_send_file_streams_videos_with_metadata(tmp_path):
    path = tmp_path / "clip.mp4"
    path.write_bytes(b"data")
    message = AsyncMock()
    result = DownloadResult(str(path), 4, "Clip", duration=65, width=1920, height=1080)
    await send_file(message, str(path), "cap", result=result)
    kwargs = message.answer_video.call_args.kwargs
    assert kwargs["supports_streaming"] is True
    assert kwargs["width"] == 1920 and kwargs["height"] == 1080
    assert kwargs["duration"] == 65


@pytest.mark.asyncio
async def test_cancel_without_a_job_says_so():
    from bot import cmd_cancel

    message = _message("/cancel")
    with patch.dict("bot._cancel_flags", {}, clear=True):
        await cmd_cancel(message)
    assert "Nothing is running" in message.answer.call_args.args[0]


@pytest.mark.asyncio
async def test_cancel_flags_a_running_job():
    import asyncio

    from bot import cmd_cancel

    event = asyncio.Event()
    message = _message("/cancel")
    with patch.dict("bot._cancel_flags", {123: event}, clear=True):
        await cmd_cancel(message)
    assert event.is_set()
    assert "cancelled" in message.answer.call_args.args[0].lower()


def test_upload_limit_matches_api_mode():
    import bot

    assert bot.UPLOAD_LIMIT_MB == (2000 if bot.TELEGRAM_API_URL else 50)


def test_quality_ladder_prefers_progressive_and_respects_cap():
    from bot import video_format

    cap = 50 * 1024 * 1024
    ladder = video_format("360", cap)
    first = ladder.split("/")[0]
    assert first.startswith("b[height<=360]")       # single file, no merge
    assert f"filesize<{cap}" in first               # capped before download

    best = video_format("best", cap)
    assert best.startswith("b[") and f"filesize<{cap}" in best.split("/")[0]


@pytest.mark.asyncio
async def test_upload_progress_never_exceeds_total(tmp_path):
    from upload_utils import ProgressFSInputFile

    path = tmp_path / "clip.mp4"
    path.write_bytes(b"a" * 10)
    reports = []

    source = ProgressFSInputFile(str(path), chunk_size=4)
    source.progress_callback = lambda sent, total, speed, eta: reports.append(sent)

    await _drain(source)
    await _drain(source)  # aiogram may read the same source twice

    assert reports and max(reports) <= 10


async def _drain(source):
    from unittest.mock import MagicMock

    async for _ in source.read(MagicMock()):
        pass


def test_stale_files_are_swept_but_active_ones_stay(tmp_path, monkeypatch):
    import os
    import time

    import bot

    stale = bot.DOWNLOADS_DIR / "9911_old.bin"
    stale.write_bytes(b"x")
    active = bot.DOWNLOADS_DIR / "9912_busy.bin"
    active.write_bytes(b"x")
    old = time.time() - 7_200
    os.utime(stale, (old, old))
    os.utime(active, (old, old))

    monkeypatch.setattr(bot, "TEMP_FILE_TTL", 60)
    monkeypatch.setitem(bot._inflight, 9912, 42)
    try:
        bot._sweep_temp_files()
        assert not stale.exists()
        assert active.exists()
    finally:
        bot._inflight.pop(9912, None)
        active.unlink(missing_ok=True)


@pytest.mark.asyncio
async def test_per_user_pending_cap(monkeypatch):
    import bot

    class Status:
        def __init__(self):
            self.text = ""

        async def edit_text(self, text, **kwargs):
            self.text = text

    class Message:
        class from_user:
            id = 4242

    monkeypatch.setattr(bot, "MAX_PENDING_PER_USER", 1)
    monkeypatch.setattr(bot.db, "update_download_status", lambda *a, **k: None)
    bot._inflight[1234] = 4242
    status = Status()
    try:
        accepted = await bot._enqueue(Message(), status, "http://x/y", 5678, "h", user_id=4242)
    finally:
        bot._inflight.pop(1234, None)
    assert accepted is False
    assert "Too many" in status.text


@pytest.mark.asyncio
async def test_failed_upload_still_deletes_the_file(tmp_path):
    import bot
    from downloader import DownloadResult

    produced = bot.DOWNLOADS_DIR / "5555_clip.mp4"
    produced.write_bytes(b"z" * 32)

    async def fake_download(*args, **kwargs):
        return DownloadResult(path=str(produced), size=32, title="clip")

    status = AsyncMock()
    message = _message("https://example.com/clip.mp4")

    with (
        patch("bot.download_file", fake_download),
        patch("bot.send_file", AsyncMock(side_effect=RuntimeError("telegram died"))),
        patch("bot.db.update_download_status"),
        patch("bot.safe_edit", AsyncMock()),
    ):
        await bot.execute_download(message, status, "https://example.com/clip.mp4", 5555, "h")

    assert not produced.exists()
    assert not list(bot.DOWNLOADS_DIR.glob("5555_*"))
    assert 5555 not in bot._inflight
