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
from linktofile.application.preferences import UserPreferences
from linktofile.application.preview import PreviewService
from linktofile.application.queue import DownloadQueue
from linktofile.application.ratelimit import RateLimiter
from linktofile.application.settings import AccessGuard, SettingsService
from linktofile.config import (
    ADMIN_ID,
    BOT_TOKEN,
    DOWNLOADS_DIR,
    DOWNLOAD_RETRIES,
    MAX_PENDING_PER_USER,
    MIN_FREE_DISK_MB,
    PREVIEW_TIMEOUT,
    PREVIEW_WAIT,
    QUEUE_CONCURRENCY,
    QUEUE_MAX_SIZE,
    QUEUE_TASK_TIMEOUT,
    RATE_LIMIT_SECONDS,
    RESUME_MAX_JOBS,
    RESUME_ON_STARTUP,
    RETRY_BACKOFF_MAX,
    RETRY_BACKOFF_SECONDS,
    TELEGRAM_API_URL,
    UPLOAD_LIMIT_BYTES,
    UPLOAD_LIMIT_MB,
    DB_PATH,
)
from linktofile.domain.models import FailureKind
from linktofile.domain.policies import FormatPolicy
from linktofile.infrastructure.download.adapter import EngineDownloadAdapter
from linktofile.infrastructure.download.preview import YtDlpProbe
from linktofile.infrastructure.membership import TelegramMembership
from linktofile.infrastructure.persistence.sqlite import (
    Database,
    SqliteSettings,
    SqliteStats,
    SqliteTickets,
    SqliteUsers,
)
from linktofile.infrastructure.system.version import VERSION
from linktofile.application.recovery import QueueRecovery
from linktofile.infrastructure.telegram.app import build_dispatcher
from linktofile.infrastructure.telegram.resume import HeadlessDelivery

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
        retries=DOWNLOAD_RETRIES,
        backoff_seconds=float(RETRY_BACKOFF_SECONDS),
        backoff_max=float(RETRY_BACKOFF_MAX),
    )

    def _record_failure(ticket_id: int, kind: str, reason: str) -> None:
        """Queue-level failure (task timeout): persist it, never raise."""
        try:
            tickets.fail(ticket_id, kind=FailureKind(kind), error=reason)
        except Exception as exc:  # noqa: BLE001 - accounting is best effort
            logger.warning("Could not record queue failure for %s: %s", ticket_id, exc)

    queue = DownloadQueue(
        concurrency=QUEUE_CONCURRENCY,
        max_size=QUEUE_MAX_SIZE,
        per_user_limit=MAX_PENDING_PER_USER,
        task_timeout=QUEUE_TASK_TIMEOUT,
        on_failure=_record_failure,
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
        preview=PreviewService(YtDlpProbe(), timeout=PREVIEW_TIMEOUT, wait=PREVIEW_WAIT),
        prefs=UserPreferences(settings_repo),
        cap_mb=UPLOAD_LIMIT_MB,
        admin_id=ADMIN_ID,
        version=VERSION,
    )

    if RESUME_ON_STARTUP:
        services.recovery = QueueRecovery(
            tickets=tickets,
            queue=queue,
            workflow=workflow,
            active=services.active,
            delivery_factory=lambda item: HeadlessDelivery(
                bot, item.user_id, ticket_id=item.ticket_id, admin_id=ADMIN_ID
            ),
            max_jobs=RESUME_MAX_JOBS,
        )

    logger.info(
        "Wired: db=%s cap=%sMB workers=%s preview=%ss/%ss",
        DB_PATH,
        UPLOAD_LIMIT_MB,
        QUEUE_CONCURRENCY,
        PREVIEW_WAIT,
        PREVIEW_TIMEOUT,
    )
    return Application(bot=bot, dispatcher=build_dispatcher(services), services=services)
