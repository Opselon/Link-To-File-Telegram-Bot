"""The download use case: guard, fetch, deliver, clean up.

The workflow knows nothing about Telegram: it talks to a ``Delivery`` port
that the Telegram adapter implements. That keeps the ordering rules (status
transitions, size cap, cleanup) testable without a bot.
"""

from __future__ import annotations

import asyncio
import logging
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Protocol
from urllib.parse import urlparse

from linktofile.domain.errors import (
    BotError,
    DownloadCancelled,
    MediaTooLarge,
    NotEnoughDisk,
)
from linktofile.domain.models import MediaAsset, Quality
from linktofile.domain.policies import FormatPolicy
from linktofile.domain.ports import DownloadEngine, TicketRepository
from linktofile.shared.telemetry import Timer, pipeline

logger = logging.getLogger(__name__)


def _host(url: str) -> str:
    try:
        return (urlparse(url).hostname or "").lower()
    except ValueError:  # pragma: no cover - defensive
        return ""


@dataclass(frozen=True, slots=True)
class DownloadJob:
    ticket_id: int
    user_id: int
    url: str
    quality: Quality


class Delivery(Protocol):
    """Everything the workflow needs from the chat surface."""

    async def delivering(self, asset: MediaAsset) -> None: ...

    async def delivered(self, asset: MediaAsset) -> None: ...

    async def failed(self, error: BotError) -> None: ...

    async def cancelled(self) -> None: ...


class DownloadWorkflow:
    def __init__(
        self,
        *,
        engine: DownloadEngine,
        tickets: TicketRepository,
        policy: FormatPolicy,
        temp_dir: Path,
        min_free_mb: int = 512,
    ) -> None:
        self._engine = engine
        self._tickets = tickets
        self._policy = policy
        self._temp_dir = Path(temp_dir)
        self._min_free_mb = min_free_mb

    @property
    def temp_dir(self) -> Path:
        return self._temp_dir

    async def close(self) -> None:
        closer = getattr(self._engine, "close", None)
        if closer is not None:
            await closer()


    # --- public API ---------------------------------------------------------
    async def run(
        self,
        job: DownloadJob,
        delivery: Delivery,
        *,
        on_progress=None,
        cancel_event: Optional[asyncio.Event] = None,
    ) -> None:
        asset: Optional[MediaAsset] = None
        timer = Timer()
        host = _host(job.url)
        pipeline(
            "job.start",
            ticket=job.ticket_id,
            user=job.user_id,
            host=host,
            quality=job.quality.label,
        )
        try:
            self._guard_disk()
            self._tickets.update(job.ticket_id, _status("downloading"))
            asset = await self._engine.fetch(
                job.url,
                job.ticket_id,
                job.quality,
                on_progress=on_progress,
                cancel_event=cancel_event,
            )
            pipeline(
                "job.fetched",
                ticket=job.ticket_id,
                host=host,
                bytes=asset.size,
                ms=timer.elapsed,
                source=asset.source,
            )
            if not self._policy.accepts(asset.size):
                raise MediaTooLarge(
                    f"This file is {asset.size // (1024 * 1024)} MB, but the bot can "
                    f"deliver at most {self._policy.cap_mb} MB. Pick a lower quality."
                )

            self._tickets.update(
                job.ticket_id,
                _status("uploading"),
                filename=asset.path.name,
                size=asset.size,
            )
            await delivery.delivering(asset)
            await delivery.delivered(asset)
            self._tickets.update(job.ticket_id, _status("completed"))
            pipeline(
                "job.done",
                ticket=job.ticket_id,
                host=host,
                bytes=asset.size,
                ms=timer.elapsed,
            )

        except DownloadCancelled:
            self._tickets.update(job.ticket_id, _status("cancelled"))
            pipeline("job.cancelled", ticket=job.ticket_id, host=host, ms=timer.elapsed)
            await delivery.cancelled()
        except asyncio.CancelledError:
            self._tickets.update(job.ticket_id, _status("cancelled"))
            pipeline("job.cancelled", ticket=job.ticket_id, host=host, ms=timer.elapsed)
            # A fresh task survives the cancellation that is unwinding us, so the
            # user still gets the "cancelled" card even if the socket was hung.
            asyncio.create_task(_quietly(delivery.cancelled()))
            raise
        except BotError as exc:
            self._tickets.update(job.ticket_id, _status("failed"))
            logger.info("Ticket %s failed: %s", job.ticket_id, exc)
            pipeline(
                "job.failed",
                ticket=job.ticket_id,
                host=host,
                ms=timer.elapsed,
                category=type(exc).__name__,
                reason=str(exc)[:200],
                level=logging.WARNING,
            )
            await delivery.failed(exc)
        except Exception as exc:  # noqa: BLE001 - last line of defence
            self._tickets.update(job.ticket_id, _status("failed"))
            logger.exception("Unexpected failure for ticket %s", job.ticket_id)
            pipeline(
                "job.failed",
                ticket=job.ticket_id,
                host=host,
                ms=timer.elapsed,
                category=type(exc).__name__,
                reason=str(exc)[:200],
                level=logging.ERROR,
            )
            await delivery.failed(BotError("Something broke on my side. Try again in a moment."))
        finally:
            self._discard(job.ticket_id, asset)

    # --- internals ----------------------------------------------------------
    def _guard_disk(self) -> None:
        free_mb = shutil.disk_usage(self._temp_dir).free // (1024 * 1024)
        if free_mb < self._min_free_mb:
            raise NotEnoughDisk(
                f"The server is low on free disk space ({free_mb} MB left). "
                "Try again in a little while."
            )

    def _discard(self, ticket_id: int, asset: Optional[MediaAsset]) -> None:
        """Never leave a byte behind, whichever branch we took."""
        if asset is not None:
            try:
                Path(asset.path).unlink(missing_ok=True)
            except OSError as exc:
                logger.debug("Could not delete %s: %s", asset.path, exc)
        for leftover in self._temp_dir.glob(f"{ticket_id}_*"):
            try:
                leftover.unlink(missing_ok=True)
            except OSError as exc:
                logger.debug("Could not delete %s: %s", leftover, exc)

    def sweep(self, max_age_seconds: float, keep: set[int] | None = None) -> int:
        """Delete orphaned temp files (janitor)."""
        import time

        keep = keep or set()
        now = time.time()
        removed = 0
        for path in self._temp_dir.glob("*"):
            if not path.is_file():
                continue
            try:
                ticket = int(path.name.split("_", 1)[0])
            except (ValueError, IndexError):
                ticket = -1
            if ticket in keep:
                continue
            try:
                if now - path.stat().st_mtime < max_age_seconds:
                    continue
                path.unlink(missing_ok=True)
                removed += 1
            except OSError as exc:
                logger.debug("Janitor could not remove %s: %s", path, exc)
        return removed


def _status(value: str):
    from linktofile.domain.models import DownloadStatus

    return DownloadStatus(value)


async def _quietly(coro) -> None:
    try:
        await coro
    except Exception:  # noqa: BLE001 - already tearing down
        pass
