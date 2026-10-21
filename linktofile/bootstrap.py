"""Composition root: the only place where concrete classes are wired together."""

from __future__ import annotations

import logging
from dataclasses import dataclass

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.enums import ParseMode

from linktofile.application.container import Services
from linktofile.application.active import ActiveJobs
from linktofile.application.downloads import DownloadWorkflow
from linktofile.application.queue import DownloadQueue
from linktofile.application.ratelimit import RateLimiter
from linktofile.application.settings import AccessGuard, SettingsService
from linktofile.config import (
    ADMIN_ID,
    BOT_TOKEN,
    DOWNLOADS_DIR,
    MAX_PENDING_PER_USER,
    MIN_FREE_DISK_MB,
    QUEUE_CONCURRENCY,
    QUEUE_MAX_SIZE,
    QUEUE_TASK_TIMEOUT,
    RATE_LIMIT_SECONDS,
    TELEGRAM_API_URL,
    UPLOAD_LIMIT_BYTES,
    UPLOAD_LIMIT_MB,
    DB_PATH,
)
from linktofile.domain.policies import FormatPolicy
from linktofile.infrastructure.download.adapter import EngineDownloadAdapter
from linktofile.infrastructure.membership import TelegramMembership
from linktofile.infrastructure.persistence.sqlite import (
    Database,
    SqliteSettings,
    SqliteStats,
    SqliteTickets,
    SqliteUsers,
)
from linktofile.infrastructure.system.version import VERSION
from linktofile.infrastructure.telegram.app import build_dispatcher

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class Application:
    bot: Bot
    dispatcher: Dispatcher
    services: Services

    async def close(self) -> None:
        await self.services.workflow.close()
        await self.bot.session.close()


def build_application() -> Application:
    if not BOT_TOKEN:
        raise SystemExit("BOT_TOKEN is missing — run `python main.py --setup` first.")

    session = AiohttpSession(api=TELEGRAM_API_URL) if TELEGRAM_API_URL else AiohttpSession()
    bot = Bot(
        token=BOT_TOKEN,
        session=session,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML, link_preview_is_disabled=True),
    )

    database = Database(DB_PATH)
    users = SqliteUsers(database)
    tickets = SqliteTickets(database)
    settings_repo = SqliteSettings(database)
    stats = SqliteStats(database)

    policy = FormatPolicy(cap_bytes=UPLOAD_LIMIT_BYTES)
    settings = SettingsService(settings_repo)
    membership = TelegramMembership(bot)
    access = AccessGuard(settings, membership)

    engine = EngineDownloadAdapter(policy)
    workflow = DownloadWorkflow(
        engine=engine,
        tickets=tickets,
        policy=policy,
        temp_dir=DOWNLOADS_DIR,
        min_free_mb=MIN_FREE_DISK_MB,
    )
    queue = DownloadQueue(
        concurrency=QUEUE_CONCURRENCY,
        max_size=QUEUE_MAX_SIZE,
        per_user_limit=MAX_PENDING_PER_USER,
        task_timeout=QUEUE_TASK_TIMEOUT,
    )

    services = Services(
        bot=bot,
        settings=settings,
        access=access,
        membership=membership,
        tickets=tickets,
        users=users,
        stats=stats,
        settings_repo=settings_repo,
        queue=queue,
        workflow=workflow,
        policy=policy,
        limiter=RateLimiter(RATE_LIMIT_SECONDS),
        active=ActiveJobs(),
        cap_mb=UPLOAD_LIMIT_MB,
        admin_id=ADMIN_ID,
        version=VERSION,
    )

    logger.info("Wired: db=%s cap=%sMB workers=%s", DB_PATH, UPLOAD_LIMIT_MB, QUEUE_CONCURRENCY)
    return Application(bot=bot, dispatcher=build_dispatcher(services), services=services)
