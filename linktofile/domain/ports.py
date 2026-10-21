"""Ports: contracts the application layer depends on."""

from __future__ import annotations

import asyncio
from typing import Optional, Protocol, Sequence, runtime_checkable

from linktofile.domain.models import (
    DownloadStatus,
    DownloadTicket,
    MediaAsset,
    MediaInfo,
    Quality,
)


@runtime_checkable
class DownloadEngine(Protocol):
    async def fetch(
        self,
        url: str,
        request_id: int,
        quality: Quality,
        *,
        on_progress=None,
        cancel_event: Optional[asyncio.Event] = None,
    ) -> MediaAsset: ...

    async def close(self) -> None: ...


@runtime_checkable
class MediaProbe(Protocol):
    """Reads a media page's metadata without downloading the payload."""

    async def probe(self, url: str) -> Optional[MediaInfo]: ...


@runtime_checkable
class TicketRepository(Protocol):
    def create(self, user_id: int, url: str) -> DownloadTicket: ...

    def get(self, ticket_id: int) -> Optional[DownloadTicket]: ...

    def update(
        self,
        ticket_id: int,
        status: DownloadStatus,
        *,
        filename: Optional[str] = None,
        size: Optional[int] = None,
    ) -> None: ...

    def recent(self, user_id: int, limit: int = 5) -> Sequence[DownloadTicket]: ...


@runtime_checkable
class UserRepository(Protocol):
    def register(self, telegram_id: int) -> bool: ...

    def count(self) -> int: ...


@runtime_checkable
class StatsRepository(Protocol):
    def stats(self) -> dict: ...


@runtime_checkable
class SettingsRepository(Protocol):
    def get(self, key: str, default: str = "") -> str: ...

    def set(self, key: str, value: str) -> None: ...


@runtime_checkable
class MembershipGateway(Protocol):
    async def is_member(self, user_id: int, channel: str) -> bool: ...

    async def resolve_channel(self, channel: str): ...

    def forget(self) -> None: ...


class ProgressListener(Protocol):
    def __call__(self, downloaded: int, total: int, speed: float, eta: float) -> None: ...
