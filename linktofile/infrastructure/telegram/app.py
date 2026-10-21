"""Dispatcher assembly: routers, lifecycle hooks, last-resort error handling."""

from __future__ import annotations

import asyncio
import logging

from aiogram import Bot, Dispatcher
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import BotCommand, ErrorEvent

from linktofile.application.container import Services
from linktofile.config import (
    ADMIN_ID,
    BOT_DISPLAY_NAME,
    INSTANT_HOSTS,
    JANITOR_INTERVAL,
    TEMP_FILE_TTL,
    YTDLP_COOKIES_FILE,
    YTDLP_PROXY,
)
from linktofile.infrastructure.janitor import janitor_loop
from linktofile.infrastructure.telegram import handlers
from linktofile.shared.telemetry import pipeline

logger = logging.getLogger(__name__)

COMMANDS = [
    BotCommand(command="start", description="Main menu"),
    BotCommand(command="help", description="How it works"),
    BotCommand(command="history", description="Your recent downloads"),
    BotCommand(command="stats", description="Your numbers"),
    BotCommand(command="cancel", description="Stop my downloads"),
    BotCommand(command="id", description="Show my Telegram ID"),
    BotCommand(command="version", description="Bot version"),
]

_tasks: list[asyncio.Task] = []


def build_dispatcher(services: Services) -> Dispatcher:
    dispatcher = Dispatcher(storage=MemoryStorage())
    dispatcher["services"] = services
    dispatcher.include_router(handlers.router)
    dispatcher.startup.register(on_startup)
    dispatcher.shutdown.register(on_shutdown)
    dispatcher.errors.register(on_error)
    return dispatcher


async def on_startup(services: Services, bot: Bot) -> None:
    services.queue.start()
    _tasks.append(
        asyncio.create_task(
            janitor_loop(
                services.workflow.sweep,
                interval=JANITOR_INTERVAL,
                max_age=TEMP_FILE_TTL,
                active=lambda: services.active.all(),
            ),
            name="janitor",
        )
    )

    # A restart strands whatever the last process had in flight: the queue is
    # in memory, so those tickets would stay "downloading" forever.
    reaped = services.tickets.reap_stale()
    if reaped:
        logger.info("Reaped %s stale ticket(s) from the previous run", reaped)

    try:
        await bot.set_my_commands(COMMANDS)
        if BOT_DISPLAY_NAME:
            await bot.set_my_name(BOT_DISPLAY_NAME)
        await bot.set_my_description(
            "Send any link and get the file back. Video, audio or direct downloads — "
            "fast, free and private."
        )
    except Exception as exc:  # noqa: BLE001 - cosmetics must not stop the bot
        logger.debug("Bot profile update skipped: %s", exc)

    policy = services.settings.force_join()
    pipeline(
        "boot",
        version=services.version,
        cap_mb=services.cap_mb,
        workers=services.queue.concurrency,
        instant=",".join(INSTANT_HOSTS),
        cookies="yes" if YTDLP_COOKIES_FILE else "no",
        proxy="yes" if YTDLP_PROXY else "no",
        ytdlp=_ytdlp_version(),
        impersonate=_impersonation(),
        admin=ADMIN_ID or "unset",
    )
    logger.info(
        "Ready · v%s · admin=%s · cap=%sMB · force_join=%s%s",
        services.version,
        ADMIN_ID or "unset",
        services.cap_mb,
        "on" if policy.is_active else "off",
        f" · {policy.channel}" if policy.channel else "",
    )


def _ytdlp_version() -> str:
    """The extractor version decides whether Instagram/YouTube work at all."""
    try:
        from importlib.metadata import version

        return version("yt-dlp")
    except Exception:  # noqa: BLE001 - cosmetic only
        return "unknown"


def _impersonation() -> str:
    """curl_cffi lets yt-dlp fake a browser TLS fingerprint when a site blocks us."""
    try:
        import curl_cffi  # noqa: F401

        return "yes"
    except Exception:  # noqa: BLE001 - optional dependency
        return "no"


async def on_shutdown(services: Services, bot: Bot) -> None:
    for task in _tasks:
        task.cancel()
    for task in _tasks:
        try:
            await task
        except (asyncio.CancelledError, Exception):  # noqa: BLE001
            pass
    _tasks.clear()
    await services.queue.drain()
    await services.workflow.close()
    logger.info("Shutdown complete")


async def on_error(event: ErrorEvent) -> bool:
    logger.exception("Unhandled update error: %s", event.exception)
    update = event.update
    message = update.message or (update.callback_query.message if update.callback_query else None)
    if message is not None:
        try:
            await message.answer("⚠️ Something broke on my side. Try that again?")
        except Exception:  # noqa: BLE001
            pass
    return True


__all__ = ["build_dispatcher", "COMMANDS"]
