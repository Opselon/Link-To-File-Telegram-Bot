"""Telegram rate-limit handling in one place.

``TelegramRetryAfter`` is the one failure the platform *tells* us the cure for
(``retry_after`` seconds). Undoing a flood wait inside the delivery, rather than
failing the ticket, is what keeps queued work alive while the bot runs hot.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Awaitable, Callable, TypeVar

from aiogram.exceptions import TelegramRetryAfter

from linktofile.domain.errors import BotError
from linktofile.shared.telemetry import pipeline

logger = logging.getLogger(__name__)

T = TypeVar("T")


async def send_with_flood_wait(
    action: Callable[[], Awaitable[T]],
    *,
    ticket: Any = None,
    stage: str = "send",
    max_wait: float = 60.0,
    attempts: int = 2,
    sleep: Callable[[float], Any] = asyncio.sleep,
) -> T:
    """Run a Telegram call, honouring at most one retry-after window.

    A wait longer than ``max_wait`` is not slept away — the card editor would
    sit silently for minutes and the user deserves a truthful answer instead.
    """
    for attempt in range(1, max(1, attempts) + 1):
        try:
            return await action()
        except TelegramRetryAfter as exc:
            wait = min(float(getattr(exc, "retry_after", 1.0) or 1.0), max_wait)
            if float(getattr(exc, "retry_after", 1.0) or 1.0) > max_wait:
                pipeline(
                    "flood.wait",
                    ticket=ticket,
                    op=stage,
                    wait_s=float(getattr(exc, "retry_after", 0.0)),
                    result="refused",
                    level=logging.WARNING,
                )
                raise BotError(
                    "Telegram asked me to wait a while before sending more "
                    "files. Please try again in a few minutes."
                ) from exc
            pipeline(
                "flood.wait",
                ticket=ticket,
                op=stage,
                wait_s=wait,
                attempt=attempt,
                level=logging.WARNING,
            )
            if attempt >= attempts:
                raise BotError(
                    "Telegram is asking me to slow down. Give it a moment "
                    "and try again."
                ) from exc
            await sleep(wait)
    raise BotError("Telegram is busy right now. Try again shortly.")


__all__ = ["send_with_flood_wait"]
