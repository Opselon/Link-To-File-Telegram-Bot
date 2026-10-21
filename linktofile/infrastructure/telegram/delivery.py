"""Telegram side of a download: the live card and the actual file upload."""

from __future__ import annotations

import asyncio
import html
import logging
import time
from collections import deque
from typing import Optional

from aiogram import Bot
from aiogram.exceptions import TelegramBadRequest, TelegramRetryAfter
from aiogram.types import FSInputFile, InlineKeyboardMarkup, Message

from linktofile.domain.errors import BotError
from linktofile.domain.models import MediaAsset
from linktofile.infrastructure.telegram import keyboards, texts
from linktofile.infrastructure.telegram.upload import ProgressFSInputFile
from linktofile.shared.telemetry import Timer, pipeline
from linktofile.shared.utils import format_size, get_progress_bar

logger = logging.getLogger(__name__)

VIDEO_EXT = {".mp4", ".mkv", ".mov", ".avi", ".webm", ".m4v"}
AUDIO_EXT = {".mp3", ".m4a", ".wav", ".flac", ".ogg", ".opus"}

# Operator alerts are throttled per process: a burst of dead links must not turn
# into a burst of messages. Ten per hour is plenty to notice a real outage.
_ALERT_WINDOW = 3600.0
_ALERT_LIMIT = 10
_ALERT_TIMES: deque[float] = deque()

# Fire-and-forget card refreshes need a strong reference until they finish,
# otherwise the event loop can garbage-collect them mid-flight.
_CARD_TASKS: set[asyncio.Task] = set()


def _keep(task: asyncio.Task) -> None:
    _CARD_TASKS.add(task)
    task.add_done_callback(_CARD_TASKS.discard)


class Card:
    """Throttled, failure-tolerant editor for one status message."""

    def __init__(self, message: Message, *, interval: float = 1.5) -> None:
        self._message = message
        self._interval = interval
        self._last = 0.0
        self._last_text = ""
        self._closed = False
        # The card is built inside a handler, so a loop is running here. Keep it:
        # progress hooks fire later from worker threads and must hop back onto it.
        try:
            self._loop: Optional[asyncio.AbstractEventLoop] = asyncio.get_running_loop()
        except RuntimeError:  # pragma: no cover - only outside a running bot
            self._loop = None

    async def show(
        self,
        text: str,
        *,
        markup: Optional[InlineKeyboardMarkup] = None,
        force: bool = False,
    ) -> None:
        if self._closed:
            return
        now = time.monotonic()
        if not force and now - self._last < self._interval:
            return
        if text == self._last_text and not force:
            return
        self._last, self._last_text = now, text
        try:
            await self._message.edit_text(text, parse_mode="HTML", reply_markup=markup)
        except TelegramRetryAfter as exc:
            await asyncio.sleep(min(exc.retry_after, 5))
        except TelegramBadRequest as exc:
            if "message is not modified" in str(exc):
                return
            if "not found" in str(exc) or "can't be edited" in str(exc):
                self._closed = True
                return
            logger.debug("card edit failed: %s", exc)
        except Exception as exc:  # noqa: BLE001 - a card must never break a job
            logger.debug("card edit error: %s", exc)

    def schedule(
        self, text: str, *, markup: Optional[InlineKeyboardMarkup] = None
    ) -> None:
        """Refresh the card without blocking, from any thread.

        yt-dlp's progress hooks and the upload stream run off-loop (worker
        threads), where ``asyncio.create_task`` raises "no running event loop".
        That used to surface as a failed ticket, so hop back onto the loop that
        owns this card instead — and never raise into the caller.
        """
        coro = self.show(text, markup=markup)
        loop = self._loop
        if loop is None or loop.is_closed():
            coro.close()  # nothing to update; never leak an un-awaited coroutine
            return
        try:
            running = asyncio.get_running_loop()
        except RuntimeError:
            running = None
        if running is loop:
            _keep(asyncio.create_task(coro))
            return

        def _start() -> None:
            _keep(asyncio.create_task(coro))

        try:
            loop.call_soon_threadsafe(_start)
        except RuntimeError:  # loop closed between the check and the hop
            coro.close()

    async def close(self) -> None:
        self._closed = True
        try:
            await self._message.delete()
        except Exception:  # noqa: BLE001
            pass


class TelegramDelivery:
    """Implements the application ``Delivery`` port for one chat."""

    def __init__(
        self,
        bot: Bot,
        message: Message,
        *,
        ticket_id: int,
        header: str,
        media: bool,
        card: Optional[Card] = None,
        admin_id: Optional[int] = None,
    ) -> None:
        self._bot = bot
        self._message = message
        self._ticket = ticket_id
        self._header = header
        self._media = media
        self._admin_id = admin_id
        self.card = card or Card(message)

    # --- progress (called from the engine, possibly a worker thread) --------
    def progress(self, downloaded: int, total: int, speed: float, eta: float) -> None:
        text = texts.progress_text(
            self._header,
            get_progress_bar(downloaded, total, speed, eta, stage="⬇️ Downloading"),
        )
        self.card.schedule(text, markup=keyboards.progress_menu(self._ticket))

    # --- Delivery port ------------------------------------------------------
    async def delivering(self, asset: MediaAsset) -> None:
        await self.card.show(
            f"{self._header}\n\n⬆️ <b>Uploading</b> · {format_size(asset.size)}",
            markup=keyboards.progress_menu(self._ticket),
            force=True,
        )

    async def delivered(self, asset: MediaAsset) -> None:
        source = (
            ProgressFSInputFile(str(asset.path), progress_callback=self._upload_progress)
            if asset.size > 5 * 1024 * 1024
            else FSInputFile(str(asset.path))
        )
        caption = texts.done_caption(asset)
        markup = keyboards.result_menu(self._ticket, media=self._media)
        suffix = asset.suffix
        timer = Timer()
        pipeline(
            "upload.start",
            ticket=self._ticket,
            bytes=asset.size,
            kind=suffix.lstrip(".") or "file",
        )

        if suffix in VIDEO_EXT:
            extra = {"supports_streaming": True}
            if asset.duration:
                extra["duration"] = asset.duration
            if asset.width and asset.height:
                extra.update(width=asset.width, height=asset.height)
            await self._message.answer_video(
                source, caption=caption, parse_mode="HTML", reply_markup=markup, **extra
            )
        elif suffix in AUDIO_EXT:
            extra = {}
            if asset.duration:
                extra["duration"] = asset.duration
            if asset.title:
                extra["title"] = asset.title[:64]
            if asset.artist:
                extra["performer"] = asset.artist[:64]
            await self._message.answer_audio(
                source, caption=caption, parse_mode="HTML", reply_markup=markup, **extra
            )
        else:
            await self._message.answer_document(
                source, caption=caption, parse_mode="HTML", reply_markup=markup
            )
        await self.card.close()
        pipeline("upload.done", ticket=self._ticket, bytes=asset.size, ms=timer.elapsed)

    async def failed(self, error: BotError) -> None:
        await self.card.show(
            f"❌ <b>Couldn't finish that</b>\n\n{html.escape(str(error))}",
            markup=keyboards.error_menu(self._ticket, media=self._media),
            force=True,
        )
        pipeline(
            "deliver.failed",
            ticket=self._ticket,
            category=type(error).__name__,
            reason=str(error)[:200],
            level=logging.WARNING,
        )
        await self._alert_admin(error)

    async def _alert_admin(self, error: BotError) -> None:
        """One compact line to the operator per failure, throttled.

        Users only see the friendly sentence; the operator needs the ticket, the
        site and the reason without grepping the whole log.
        """
        if not self._admin_id:
            return
        now = time.monotonic()
        while _ALERT_TIMES and now - _ALERT_TIMES[0] > _ALERT_WINDOW:
            _ALERT_TIMES.popleft()
        if len(_ALERT_TIMES) >= _ALERT_LIMIT:
            return
        _ALERT_TIMES.append(now)
        text = (
            f"⚠️ <b>ticket {self._ticket}</b> failed\n"
            f"<code>{html.escape(str(error)[:300])}</code>"
        )
        try:
            await self._bot.send_message(self._admin_id, text, parse_mode="HTML")
        except Exception as exc:  # noqa: BLE001 - an alert is never fatal
            logger.debug("Admin alert could not be delivered: %s", exc)

    async def cancelled(self) -> None:
        await self.card.show(
            texts.cancel_text(), markup=keyboards.cancel_menu(self._ticket), force=True
        )

    # --- internals ----------------------------------------------------------
    def _upload_progress(self, sent: int, total: int, speed: float, eta: float) -> None:
        text = texts.progress_text(
            f"{self._header}\n\n📦 {format_size(total)}",
            get_progress_bar(sent, total, speed, eta, stage="⬆️ Uploading"),
        )
        self.card.schedule(text, markup=keyboards.progress_menu(self._ticket))
