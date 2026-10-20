"""Telegram handlers: menus, quality picker, live progress and delivery."""

from __future__ import annotations

import asyncio
import html
import logging
import os
import shutil
import time
from typing import Optional

from aiogram import Bot, Dispatcher, F, types
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.client.telegram import TelegramAPIServer
from aiogram.exceptions import TelegramBadRequest, TelegramRetryAfter
from aiogram.filters import Command, CommandStart

import ui
from config import (
    ADMIN_ID,
    BOT_DISPLAY_NAME,
    BOT_TOKEN,
    DATA_DIR,
    MAX_FILE_SIZE_MB,
    PROGRESS_INTERVAL,
    TELEGRAM_API_URL,
)
from core.version import VERSION
from db import db
from downloader import (
    DownloadCancelled,
    DownloadError,
    DownloadResult,
    cleanup_file,
    close_session,
    download_file,
    is_instagram_url,
    is_media_url,
    is_youtube_url,
    redact_url,
)
from queue_manager import QueueFull, download_queue
from upload_utils import ProgressFSInputFile
from utils import (
    check_rate_limit,
    format_duration,
    format_size,
    get_progress_bar,
    is_safe_url,
    is_valid_url,
    rate_limit_remaining,
)
from aiogram.types import FSInputFile

logger = logging.getLogger(__name__)

# Bot API cloud uploads are capped at 50 MB; a self-hosted server raises it.
UPLOAD_LIMIT_MB = 2000 if TELEGRAM_API_URL else 50

session = None
if TELEGRAM_API_URL:
    session = AiohttpSession(api=TelegramAPIServer.from_base(TELEGRAM_API_URL))

bot = Bot(token=BOT_TOKEN or "123456:ABC-DEF1234ghIkl-zyx57W2v1u123ew11", session=session)
dp = Dispatcher()

_STARTED_AT = time.monotonic()
# Downloads currently owned by a worker: {download_id: user_id}
_inflight: dict[int, int] = {}
# Cancellation flags, one per user: {user_id: asyncio.Event}
_cancel_flags: dict[int, asyncio.Event] = {}

COMMANDS = [
    types.BotCommand(command="start", description="🏠 Main menu"),
    types.BotCommand(command="help", description="📖 How to use the bot"),
    types.BotCommand(command="stats", description="📊 Your download stats"),
    types.BotCommand(command="cancel", description="✖️ Stop your downloads"),
    types.BotCommand(command="id", description="🆔 Show your Telegram ID"),
    types.BotCommand(command="version", description="📦 Bot version"),
]


# --- Helpers ----------------------------------------------------------------
def _is_admin(user_id: int) -> bool:
    return bool(ADMIN_ID) and user_id == ADMIN_ID


async def safe_edit(message: types.Message, text: str, **kwargs) -> None:
    try:
        await message.edit_text(text, parse_mode="HTML", **kwargs)
    except TelegramRetryAfter as exc:
        await asyncio.sleep(exc.retry_after)
        try:
            await message.edit_text(text, parse_mode="HTML", **kwargs)
        except TelegramBadRequest:
            pass
    except TelegramBadRequest as exc:
        if "message is not modified" not in str(exc):
            logger.debug("edit failed: %s", exc)


async def answer_error(message: types.Message, text: str, download_id: Optional[int] = None) -> None:
    markup = ui.error_menu(download_id) if download_id else ui.main_menu()
    try:
        await message.answer(text, parse_mode="HTML", reply_markup=markup)
    except TelegramBadRequest as exc:
        logger.debug("answer failed: %s", exc)


class ProgressUpdater:
    """Throttled live progress card (also usable from worker threads)."""

    def __init__(
        self,
        message: types.Message,
        download_id: int,
        header: str,
        cancel_event: Optional[asyncio.Event] = None,
        *,
        stage: str = "⬇️ Downloading",
    ):
        self.message = message
        self.download_id = download_id
        self.header = header
        self.stage = stage
        self.cancel_event = cancel_event
        self.last_update = 0.0
        self.last_text = ""
        self.disabled = False
        try:
            self.loop = asyncio.get_running_loop()
        except RuntimeError:  # pragma: no cover - only outside a loop
            self.loop = None

    def __call__(self, downloaded: int, total: int, speed: float, eta: float) -> None:
        now = time.monotonic()
        if now - self.last_update < PROGRESS_INTERVAL:
            return
        self.last_update = now

        text = f"{self.header}\n\n{get_progress_bar(downloaded, total, speed, eta, stage=self.stage)}"
        if text == self.last_text:
            return
        self.last_text = text
        self._schedule(text)

    def set_stage(self, stage: str) -> None:
        self.stage = stage

    def _schedule(self, text: str) -> None:
        if self.disabled:
            return
        try:
            running = asyncio.get_running_loop()
        except RuntimeError:
            running = None
        if running is not None and running is self.loop:
            asyncio.create_task(self._edit(text))
        elif self.loop is not None:
            asyncio.run_coroutine_threadsafe(self._edit(text), self.loop)

    async def _edit(self, text: str) -> None:
        if self.disabled:
            return
        try:
            await self.message.edit_text(
                text,
                parse_mode="HTML",
                reply_markup=ui.progress_menu(self.download_id),
            )
        except TelegramRetryAfter as exc:
            await asyncio.sleep(min(exc.retry_after, 5))
        except TelegramBadRequest as exc:
            if "message is not modified" in str(exc):
                return
            if "message to edit not found" in str(exc) or "message can't be edited" in str(exc):
                self.disabled = True
                return
            logger.debug("progress edit failed: %s", exc)
        except Exception as exc:  # noqa: BLE001 - progress must never break a job
            logger.debug("progress edit error: %s", exc)


async def send_file(
    message: types.Message,
    file_path: str,
    caption: str,
    progress_callback=None,
    parse_mode: str = "HTML",
    reply_markup=None,
    result: Optional[DownloadResult] = None,
):
    """Send the finished file as video, audio or document."""
    ext = os.path.splitext(file_path)[1].lower()
    document = (
        ProgressFSInputFile(file_path, progress_callback=progress_callback)
        if progress_callback
        else FSInputFile(file_path)
    )

    if ext in {".mp4", ".mkv", ".mov", ".avi", ".webm"}:
        extra = {"supports_streaming": True}
        if result and result.duration:
            extra["duration"] = result.duration
        if result and result.width and result.height:
            extra["width"] = result.width
            extra["height"] = result.height
        await message.answer_video(
            document,
            caption=caption,
            parse_mode=parse_mode,
            reply_markup=reply_markup,
            **extra,
        )
    elif ext in {".mp3", ".m4a", ".wav", ".flac", ".ogg", ".opus"}:
        extra = {}
        if result:
            if result.duration:
                extra["duration"] = result.duration
            if result.artist:
                extra["performer"] = result.artist[:64]
            if result.title:
                extra["title"] = result.title[:64]
        await message.answer_audio(
            document,
            caption=caption,
            parse_mode=parse_mode,
            reply_markup=reply_markup,
            **extra,
        )
    else:
        await message.answer_document(
            document,
            caption=caption,
            parse_mode=parse_mode,
            reply_markup=reply_markup,
        )


def _detail_line(result: DownloadResult) -> str:
    bits = [f"📦 <b>{format_size(result.size)}</b>"]
    if result.height:
        bits.append(f"🎞 {result.height}p")
    if result.duration:
        bits.append(f"⏱ {format_duration(result.duration)}")
    return " · ".join(bits)


# --- Commands ---------------------------------------------------------------
@dp.message(CommandStart())
async def cmd_start(message: types.Message) -> None:
    if message.from_user:
        db.add_user(message.from_user.id)
    await message.answer(
        ui.welcome(message.from_user, is_admin=_is_admin(message.from_user.id)),
        parse_mode="HTML",
        reply_markup=ui.main_menu(is_admin=_is_admin(message.from_user.id)),
    )


@dp.message(Command("help"))
async def cmd_help(message: types.Message) -> None:
    await message.answer(ui.help_text(), parse_mode="HTML", reply_markup=ui.help_menu())


@dp.message(Command("stats"))
async def cmd_stats(message: types.Message) -> None:
    if not message.from_user:
        return
    stats = db.user_stats(message.from_user.id)
    await message.answer(
        ui.stats_text(stats), parse_mode="HTML", reply_markup=ui.stats_menu()
    )


@dp.message(Command("id"))
async def cmd_id(message: types.Message) -> None:
    await message.answer(
        f"🆔 Your ID: <code>{message.from_user.id}</code>\n"
        f"💬 Chat ID: <code>{message.chat.id}</code>",
        parse_mode="HTML",
    )


@dp.message(Command("version"))
async def cmd_version(message: types.Message) -> None:
    await message.answer(
        f"📦 <b>{ui.BRAND}</b> v{VERSION}\n"
        f'<a href="{ui.RELEASES_URL}">Latest release</a>',
        parse_mode="HTML",
        disable_web_page_preview=True,
    )


@dp.message(Command("cancel"))
async def cmd_cancel(message: types.Message) -> None:
    if not message.from_user:
        return
    flag = _cancel_flags.get(message.from_user.id)
    if flag and not flag.is_set():
        flag.set()
        await message.answer(ui.cancel_text(), parse_mode="HTML")
    else:
        await message.answer("Nothing is running right now. 🌤", parse_mode="HTML")


# --- Menus ------------------------------------------------------------------
@dp.callback_query(F.data.startswith("menu:"))
async def menu_router(callback: types.CallbackQuery) -> None:
    action = (callback.data or "").split(":", 1)[1]
    user_id = callback.from_user.id
    message = callback.message

    if action == "home":
        await safe_edit(
            message,
            ui.welcome(callback.from_user, is_admin=_is_admin(user_id)),
            reply_markup=ui.main_menu(is_admin=_is_admin(user_id)),
        )
    elif action == "how":
        await safe_edit(message, ui.help_text(), reply_markup=ui.help_menu())
    elif action == "sites":
        await safe_edit(message, ui.sites_text(), reply_markup=ui.sites_menu())
    elif action == "stats":
        await safe_edit(
            message,
            ui.stats_text(db.user_stats(user_id)),
            reply_markup=ui.stats_menu(),
        )
    elif action == "admin":
        if not _is_admin(user_id):
            await callback.answer("Admins only.", show_alert=True)
            return
        await safe_edit(
            message,
            ui.admin_text(
                db.stats(),
                uptime=_format_uptime(time.monotonic() - _STARTED_AT),
                queue_active=download_queue.active,
                queue_pending=download_queue.pending,
                disk_free=_disk_free(),
            ),
            reply_markup=ui.admin_menu(),
        )
    await callback.answer()


def _format_uptime(seconds: float) -> str:
    seconds = int(seconds)
    days, rem = divmod(seconds, 86400)
    hours, rem = divmod(rem, 3600)
    minutes = rem // 60
    if days:
        return f"{days}d {hours}h"
    if hours:
        return f"{hours}h {minutes}m"
    return f"{minutes}m"


def _disk_free() -> str:
    try:
        return format_size(shutil.disk_usage(DATA_DIR).free)
    except OSError:
        return "?"


# --- Link intake ------------------------------------------------------------
@dp.message(F.text)
async def handle_url(message: types.Message) -> None:
    text = (message.text or "").strip()
    user_id = message.from_user.id

    if not is_valid_url(text):
        return  # normal chat, not a link: stay quiet

    remaining = rate_limit_remaining(user_id)
    if remaining > 0:
        await message.answer(ui.rate_limited_text(remaining), parse_mode="HTML")
        return
    if not check_rate_limit(user_id):
        await message.answer(ui.rate_limited_text(1), parse_mode="HTML")
        return

    if not is_safe_url(text):
        await message.answer(ui.blocked_url_text(), parse_mode="HTML")
        return

    db.add_user(user_id)
    download_id = db.add_download(user_id, text)

    if is_youtube_url(text) or is_instagram_url(text):
        await message.answer(
            "🎬 <b>Media link detected</b>\n"
            f"<code>{html.escape(redact_url(text))}</code>\n\n"
            "Pick a quality to start:",
            parse_mode="HTML",
            reply_markup=ui.quality_menu(download_id),
            disable_web_page_preview=True,
        )
        return

    kind = "✨ <b>Media link</b>" if is_media_url(text) else "🔍 <b>Link detected</b>"
    status = await message.answer(
        f"{kind}\n<code>{html.escape(redact_url(text))}</code>\n\n⏳ <i>Queued…</i>",
        parse_mode="HTML",
        reply_markup=ui.progress_menu(download_id),
        disable_web_page_preview=True,
    )
    await _enqueue(message, status, text, download_id, kind, user_id=user_id)


async def _enqueue(
    message: types.Message,
    status: types.Message,
    url: str,
    download_id: int,
    header: str,
    ytdlp_options: Optional[dict] = None,
    user_id: Optional[int] = None,
) -> bool:
    if download_id in _inflight:
        await status.edit_text("⏳ This one is already running.", parse_mode="HTML")
        return False

    user_id = user_id or message.from_user.id
    cancel_event = asyncio.Event()
    _cancel_flags[user_id] = cancel_event
    _inflight[download_id] = user_id

    try:
        await download_queue.add_task(
            execute_download,
            message,
            status,
            url,
            download_id,
            header,
            ytdlp_options,
            cancel_event,
            user_id=user_id,
        )
    except QueueFull:
        _inflight.pop(download_id, None)
        await status.edit_text(ui.queue_full_text(), parse_mode="HTML")
        db.update_download_status(download_id, "failed")
        return False
    return True


@dp.callback_query(F.data.startswith("q:"))
async def quality_chosen(callback: types.CallbackQuery) -> None:
    parts = (callback.data or "").split(":")
    if len(parts) != 4:
        await callback.answer("Bad selection.", show_alert=True)
        return

    _, kind, quality, raw_id = parts
    try:
        download_id = int(raw_id)
    except ValueError:
        await callback.answer("Bad selection.", show_alert=True)
        return

    record = db.get_download(download_id)
    if not record:
        await callback.answer(ui.missing_download_text(), show_alert=True)
        return
    if record["user_id"] != callback.from_user.id:
        await callback.answer("This download belongs to someone else.", show_alert=True)
        return
    if not callback.message:
        return

    if kind == "mp3":
        options = {
            "format": "bestaudio/best",
            "postprocessors": [
                {
                    "key": "FFmpegExtractAudio",
                    "preferredcodec": "mp3",
                    "preferredquality": quality if quality.isdigit() else "192",
                }
            ],
            "merge_output_format": "mp3",
        }
        header = f"🎵 <b>Audio · {quality} kbps</b>"
    else:
        if quality == "best":
            fmt = "bv*[ext=mp4]+ba[ext=m4a]/b[ext=mp4]/bv*+ba/b"
            label = "⚡ Best"
        else:
            fmt = (
                f"bv*[height<={quality}][ext=mp4]+ba[ext=m4a]/"
                f"b[height<={quality}][ext=mp4]/"
                f"bv*[height<={quality}]+ba/b[height<={quality}]/"
                "bv*+ba/b"
            )
            label = f"🎬 {quality}p"
        options = {"format": fmt, "merge_output_format": "mp4"}
        header = f"🎬 <b>Video · {label}</b>"

    await safe_edit(
        callback.message,
        f"{header}\n\n⏳ <i>Queued…</i>",
        reply_markup=ui.progress_menu(download_id),
    )
    await _enqueue(
        callback.message,
        callback.message,
        record["url"],
        download_id,
        header,
        options,
        user_id=callback.from_user.id,
    )
    await callback.answer("Added to the queue ⚡")


# --- Job actions ------------------------------------------------------------
@dp.callback_query(F.data.startswith("job:"))
async def job_router(callback: types.CallbackQuery) -> None:
    parts = (callback.data or "").split(":")
    if len(parts) != 3:
        await callback.answer()
        return
    _, action, raw_id = parts
    try:
        download_id = int(raw_id)
    except ValueError:
        await callback.answer()
        return

    record = db.get_download(download_id)
    if not record or record["user_id"] != callback.from_user.id:
        await callback.answer("This download is no longer available.", show_alert=True)
        return

    if action == "cancel":
        flag = _cancel_flags.get(callback.from_user.id)
        if flag and not flag.is_set():
            flag.set()
        await callback.answer("Cancelling…")
        return

    if action == "quality":
        if not callback.message:
            return
        await safe_edit(
            callback.message,
            "🎚 <b>Pick another quality</b>",
            reply_markup=ui.quality_menu(download_id),
        )
        await callback.answer()
        return

    if action == "again":
        if not callback.message:
            return
        new_id = db.add_download(callback.from_user.id, record["url"])
        if is_media_url(record["url"]):
            await safe_edit(
                callback.message,
                "🎚 <b>Pick a quality</b>",
                reply_markup=ui.quality_menu(new_id),
            )
            await callback.answer()
            return
        header = "🔍 <b>Link detected</b>"
        await safe_edit(
            callback.message,
            f"{header}\n\n⏳ <i>Queued…</i>",
            reply_markup=ui.progress_menu(new_id),
        )
        await _enqueue(
            callback.message,
            callback.message,
            record["url"],
            new_id,
            header,
            user_id=callback.from_user.id,
        )
        await callback.answer("Queued ⚡")
        return

    await callback.answer()


# --- Core job ---------------------------------------------------------------
async def execute_download(
    message: types.Message,
    status_msg: types.Message,
    url: str,
    download_id: int,
    header: str,
    ytdlp_options: Optional[dict] = None,
    cancel_event: Optional[asyncio.Event] = None,
    user_id: Optional[int] = None,
) -> None:
    """Download → upload → cleanup, with a live card and friendly errors."""
    file_path: Optional[str] = None
    user_id = user_id or message.from_user.id
    cancel_event = cancel_event or _cancel_flags.get(user_id) or asyncio.Event()

    download_progress = ProgressUpdater(status_msg, download_id, header, cancel_event)

    try:
        result = _as_result(
            await download_file(
                url,
                download_id,
                ytdlp_options,
                progress_callback=download_progress,
                cancel_event=cancel_event,
            )
        )
        file_path = result.path

        if result.size > UPLOAD_LIMIT_MB * 1024 * 1024:
            raise DownloadError(
                f"The file is {format_size(result.size)}, but this bot can upload "
                f"{UPLOAD_LIMIT_MB} MB per file. A self-hosted Bot API server removes "
                "that limit."
            )

        db.update_download_status(
            download_id, "uploading", filename=os.path.basename(result.path), size=result.size
        )

        upload_progress = ProgressUpdater(
            status_msg, download_id, f"{header}\n\n{_detail_line(result)}", cancel_event,
            stage="⬆️ Uploading",
        )
        await safe_edit(
            status_msg,
            f"{header}\n\n⬆️ <b>Uploading</b> · {format_size(result.size)}",
            reply_markup=ui.progress_menu(download_id),
        )

        await send_file(
            message,
            result.path,
            caption=ui.done_caption(result),
            progress_callback=upload_progress,
            reply_markup=ui.result_menu(download_id, media=bool(ytdlp_options)),
            result=result,
        )

        db.update_download_status(download_id, "completed")
        try:
            await status_msg.delete()
        except TelegramBadRequest:
            pass

    except DownloadCancelled:
        db.update_download_status(download_id, "cancelled")
        await safe_edit(
            status_msg,
            ui.cancel_text(),
            reply_markup=ui.cancel_menu(download_id),
        )
    except DownloadError as exc:
        db.update_download_status(download_id, "failed")
        logger.info("Download %s failed for %s: %s", download_id, user_id, exc)
        await safe_edit(
            status_msg,
            f"❌ <b>Couldn't finish that</b>\n\n{html.escape(str(exc))}",
            reply_markup=ui.error_menu(download_id),
        )
    except Exception as exc:  # noqa: BLE001 - last line of defence for a job
        db.update_download_status(download_id, "failed")
        text = str(exc)
        if "Request Entity Too Large" in text or "TelegramEntityTooLarge" in type(exc).__name__:
            explanation = (
                "❌ <b>Telegram rejected the upload</b>\n\n"
                f"The Bot API cloud limit is {UPLOAD_LIMIT_MB} MB. "
                "Run a local Bot API server to send files up to 2 GB."
            )
        else:
            logger.exception("Unexpected error for user %s", user_id)
            explanation = "❌ <b>Something broke on my side.</b>\nTry again in a moment."
        await safe_edit(status_msg, explanation, reply_markup=ui.error_menu(download_id))
    finally:
        _inflight.pop(download_id, None)
        if _cancel_flags.get(user_id) is cancel_event:
            _cancel_flags.pop(user_id, None)
        if file_path:
            cleanup_file(file_path)


def _as_result(value) -> DownloadResult:
    """Accept a DownloadResult or a legacy ``(path, size, title)`` tuple."""
    if isinstance(value, DownloadResult):
        return value
    path, size, title = value
    return DownloadResult(path=str(path), size=int(size), title=str(title))


# --- Lifecycle --------------------------------------------------------------
@dp.errors()
async def on_error(event) -> bool:
    logger.exception("Unhandled update error: %s", getattr(event, "exception", event))
    return True


async def _configure_bot() -> None:
    try:
        await bot.delete_webhook(drop_pending_updates=False)
    except Exception as exc:  # noqa: BLE001
        logger.debug("delete_webhook failed: %s", exc)
    try:
        await bot.set_my_commands(COMMANDS)
        await bot.set_my_short_description(
            f"{ui.TAGLINE} Send a link, get a file. Free and fast."
        )
        await bot.set_my_description(
            f"⚡ {ui.BRAND} — send any public link (YouTube, Instagram, TikTok, "
            "direct files…) and get it back as a file. Tap /start to begin."
        )
        if BOT_DISPLAY_NAME:
            await bot.set_my_name(BOT_DISPLAY_NAME)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Could not update bot profile: %s", exc)


async def _notify_admin() -> None:
    if not ADMIN_ID:
        return
    marker = DATA_DIR / ".startup-notify"
    try:
        if marker.exists() and time.time() - marker.stat().st_mtime < 600:
            return
        marker.write_text(str(int(time.time())))
    except OSError:
        pass
    try:
        await bot.send_message(
            ADMIN_ID,
            f"🚀 <b>{ui.BRAND}</b> is online · v{VERSION}\n"
            f"⚙️ {download_queue.concurrency} workers · max {MAX_FILE_SIZE_MB} MB",
            parse_mode="HTML",
            reply_markup=ui.main_menu(is_admin=True),
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("Admin notification failed: %s", exc)


async def start_bot() -> None:
    logger.info("Starting %s v%s", ui.BRAND, VERSION)
    me = await bot.get_me()
    logger.info("Authenticated as @%s (id=%s)", me.username, me.id)
    await _configure_bot()
    download_queue.start()
    await _notify_admin()
    logger.info(
        "Ready · workers=%s · max_file=%sMB · api=%s",
        download_queue.concurrency,
        MAX_FILE_SIZE_MB,
        TELEGRAM_API_URL or "cloud",
    )
    try:
        await dp.start_polling(
            bot,
            allowed_updates=dp.resolve_used_update_types(),
            handle_signals=False,
        )
    finally:
        logger.info("Shutting down…")
        await download_queue.drain(timeout=20)
        await download_queue.stop()
        await close_session()
        try:
            await bot.session.close()
        except Exception:  # noqa: BLE001
            pass
        logger.info("Stopped cleanly.")
