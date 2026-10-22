"""Delivery for jobs resumed after a restart.

A resumed ticket has no message to edit any more — the previous process is gone
— so the result is sent as a fresh chat message. Everything else (caption,
media kind, keyboard, flood handling) matches the live delivery path.
"""

from __future__ import annotations

import logging
from typing import Any, Optional

from aiogram import Bot
from aiogram.types import FSInputFile

from linktofile.domain.errors import BotError
from linktofile.domain.models import MediaAsset
from linktofile.infrastructure.telegram import keyboards, texts
from linktofile.infrastructure.telegram.delivery import AUDIO_EXT, VIDEO_EXT
from linktofile.infrastructure.telegram.flood import send_with_flood_wait
from linktofile.shared.telemetry import Timer, pipeline

logger = logging.getLogger(__name__)


class HeadlessDelivery:
    """Sends a resumed job's file straight to the user's private chat."""

    def __init__(
        self,
        bot: Bot,
        chat_id: int,
        *,
        ticket_id: int,
        admin_id: Optional[int] = None,
    ) -> None:
        self._bot = bot
        self._chat_id = chat_id
        self._ticket = ticket_id
        self._admin_id = admin_id
        self.sent: list[int] = []

    async def delivering(self, asset: MediaAsset) -> None:
        # No card survives a restart; the file itself is the notification.
        logger.info("Resumed ticket %s finished downloading, uploading", self._ticket)

    async def delivered(self, asset: MediaAsset) -> None:
        caption = texts.done_caption(asset)
        markup = keyboards.result_menu(self._ticket, media=asset.source != "direct")
        source = FSInputFile(str(asset.path))
        suffix = asset.suffix
        timer = Timer()
        pipeline("upload.start", ticket=self._ticket, bytes=asset.size, resumed=True)

        async def send():
            if suffix in VIDEO_EXT:
                extra: dict[str, Any] = {"supports_streaming": True}
                if asset.duration:
                    extra["duration"] = asset.duration
                if asset.width and asset.height:
                    extra.update(width=asset.width, height=asset.height)
                return await self._bot.send_video(
                    self._chat_id,
                    source,
                    caption=caption,
                    parse_mode="HTML",
                    reply_markup=markup,
                    **extra,
                )
            if suffix in AUDIO_EXT:
                extra = {}
                if asset.duration:
                    extra["duration"] = asset.duration
                if asset.title:
                    extra["title"] = asset.title[:64]
                if asset.artist:
                    extra["performer"] = asset.artist[:64]
                return await self._bot.send_audio(
                    self._chat_id,
                    source,
                    caption=caption,
                    parse_mode="HTML",
                    reply_markup=markup,
                    **extra,
                )
            return await self._bot.send_document(
                self._chat_id,
                source,
                caption=caption,
                parse_mode="HTML",
                reply_markup=markup,
            )

        try:
            message = await send_with_flood_wait(send, ticket=self._ticket, stage="resume")
        except BotError as exc:
            raise exc
        except Exception as exc:  # noqa: BLE001 - a dead chat is a normal failure
            logger.warning("Resumed upload failed for ticket %s: %s", self._ticket, exc)
            raise BotError("Telegram refused the resumed upload.") from exc

        self.sent.append(self._ticket)
        pipeline("upload.done", ticket=self._ticket, bytes=asset.size, ms=timer.elapsed, resumed=True)
        logger.info("Resumed ticket %s delivered to chat %s", self._ticket, self._chat_id)
        _ = message  # the returned Message is not needed after a successful send

    async def failed(self, error: BotError) -> None:
        try:
            await self._bot.send_message(
                self._chat_id,
                "❌ <b>A resumed download failed</b>\n\n"
                f"<code>{error}</code>\n\nTap /history and retry the link if you still need it.",
                parse_mode="HTML",
            )
        except Exception as exc:  # noqa: BLE001 - never fatal
            logger.debug("Could not report resumed failure: %s", exc)
        if self._admin_id:
            try:
                await self._bot.send_message(
                    self._admin_id,
                    f"⚠️ Resumed ticket {self._ticket} failed: <code>{error}</code>",
                    parse_mode="HTML",
                )
            except Exception:  # noqa: BLE001 - throttling is Telegram's business
                pass

    async def cancelled(self) -> None:
        try:
            await self._bot.send_message(
                self._chat_id,
                texts.cancel_text(),
                parse_mode="HTML",
                reply_markup=keyboards.cancel_menu(self._ticket),
            )
        except Exception as exc:  # noqa: BLE001
            logger.debug("Could not report resumed cancellation: %s", exc)


__all__ = ["HeadlessDelivery"]
