"""Telegram side of a download: the live card and the actual file upload."""

from __future__ import annotations

import asyncio
import html
import logging
import time
from typing import Optional

from aiogram import Bot
from aiogram.exceptions import TelegramBadRequest, TelegramRetryAfter
from aiogram.types import FSInputFile, InlineKeyboardMarkup, Message

from linktofile.domain.errors import BotError
from linktofile.domain.models import MediaAsset
from linktofile.infrastructure.telegram import keyboards, texts
from linktofile.infrastructure.telegram.upload import ProgressFSInputFile
from linktofile.shared.utils import format_size, get_progress_bar

logger = logging.getLogger(__name__)

VIDEO_EXT = {".mp4", ".mkv", ".mov", ".avi", ".webm", ".m4v"}
AUDIO_EXT = {".mp3", ".m4a", ".wav", ".flac", ".ogg", ".opus"}


class Card:
    """Throttled, failure-tolerant editor for one status message."""

    def __init__(self, message: Message, *, interval: float = 1.5) -> None:
        self._message = message
        self._interval = interval
        self._last = 0.0
        self._last_text = ""
        self._closed = False

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
    ) -> None:
        self._bot = bot
        self._message = message
        self._ticket = ticket_id
        self._header = header
        self._media = media
        self.card = card or Card(message)

    # --- progress (called from the engine, possibly a worker thread) --------
    def progress(self, downloaded: int, total: int, speed: float, eta: float) -> None:
        text = texts.progress_text(
            self._header,
            get_progress_bar(downloaded, total, speed, eta, stage="⬇️ Downloading"),
        )
        asyncio.create_task(self.card.show(text, markup=keyboards.progress_menu(self._ticket)))

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

    async def failed(self, error: BotError) -> None:
        await self.card.show(
            f"❌ <b>Couldn't finish that</b>\n\n{html.escape(str(error))}",
            markup=keyboards.error_menu(self._ticket),
            force=True,
        )

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
        asyncio.create_task(self.card.show(text, markup=keyboards.progress_menu(self._ticket)))
