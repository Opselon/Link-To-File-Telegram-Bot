"""Telegram membership adapter with a short-lived cache.

``getChatMember`` is cheap but not free; a 60 s cache keeps a busy bot from
hammering the API while still noticing a fresh join almost immediately.
"""

from __future__ import annotations

import logging
import time
from typing import Optional

from aiogram import Bot
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import Chat

logger = logging.getLogger(__name__)

MEMBER_STATUSES = {"creator", "administrator", "member"}


class TelegramMembership:
    def __init__(self, bot: Bot, ttl: int = 60) -> None:
        self._bot = bot
        self._ttl = max(5, int(ttl))
        self._cache: dict[tuple[int, str], tuple[bool, float]] = {}

    async def is_member(self, user_id: int, channel: str) -> bool:
        if not channel:
            return True
        key = (user_id, channel)
        cached = self._cache.get(key)
        now = time.monotonic()
        if cached and now - cached[1] < self._ttl:
            return cached[0]

        try:
            member = await self._bot.get_chat_member(chat_id=channel, user_id=user_id)
            allowed = member.status in MEMBER_STATUSES
        except TelegramBadRequest as exc:
            detail = str(exc).lower()
            if "user not found" in detail or "participant_id_invalid" in detail:
                allowed = False
            else:
                # Misconfigured channel or the bot is not an admin there. Failing
                # open keeps the bot usable; the dashboard warns the operator.
                logger.warning("Membership check failed for %s: %s", channel, exc)
                allowed = True
        except Exception as exc:  # noqa: BLE001 - network hiccups must not block users
            logger.warning("Membership check error for %s: %s", channel, exc)
            allowed = True

        self._cache[key] = (allowed, now)
        return allowed

    async def resolve_channel(self, channel: str) -> Optional[Chat]:
        """Validate a channel and return its chat object (for the dashboard)."""
        try:
            return await self._bot.get_chat(channel)
        except Exception as exc:  # noqa: BLE001 - surfaced to the admin
            logger.info("Channel %s could not be resolved: %s", channel, exc)
            return None

    def forget(self, user_id: Optional[int] = None) -> None:
        if user_id is None:
            self._cache.clear()
            return
        for key in [k for k in self._cache if k[0] == user_id]:
            self._cache.pop(key, None)
