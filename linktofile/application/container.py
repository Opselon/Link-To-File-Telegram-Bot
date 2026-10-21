"""The composition root's service bundle.

Handlers receive this object, never module-level globals, which keeps them
testable and the dependencies explicit.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from aiogram import Bot

from linktofile.application.active import ActiveJobs
from linktofile.application.downloads import DownloadWorkflow
from linktofile.application.queue import DownloadQueue
from linktofile.application.ratelimit import RateLimiter
from linktofile.application.settings import AccessGuard, SettingsService
from linktofile.domain.policies import FormatPolicy
from linktofile.infrastructure.membership import TelegramMembership
from linktofile.infrastructure.persistence.sqlite import (
    SqliteSettings,
    SqliteStats,
    SqliteTickets,
    SqliteUsers,
)


@dataclass(slots=True)
class Services:
    bot: Bot
    settings: SettingsService
    access: AccessGuard
    membership: TelegramMembership
    tickets: SqliteTickets
    users: SqliteUsers
    stats: SqliteStats
    settings_repo: SqliteSettings
    queue: DownloadQueue
    workflow: DownloadWorkflow
    policy: FormatPolicy
    limiter: RateLimiter
    active: ActiveJobs
    cap_mb: int
    admin_id: Optional[int] = None
    version: str = ""

    def is_admin(self, user_id: int) -> bool:
        return bool(self.admin_id and user_id == self.admin_id)
