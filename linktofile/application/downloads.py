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
from typing import Any, Callable, Optional, Protocol
from urllib.parse import urlparse

from linktofile.domain.errors import (
    BotError,
    DownloadCancelled,
    MediaTooLarge,
    NotEnoughDisk,
    TransientDownloadFailed,
)
from linktofile.domain.models import (
    FailureKind,
    MediaAsset,
    Quality,
    failure_kind_of,
)
from linktofile.domain.policies import FormatPolicy
from linktofile.domain.ports import DownloadEngine, TicketRepository
from linktofile.shared.telemetry import Timer, pipeline

logger = logging.getLogger(__name__)


def _host(url: str) -> str:
    try:
        return (urlparse(url).hostname or "").lower()
    except ValueError:  # pragma: no cover - defensive
        return ""


def _check_cancel(cancel_event: Optional[asyncio.Event]) -> None:
    if cancel_event is not None and cancel_event.is_set():
        raise DownloadCancelled()


@dataclass(frozen=True, slots=True)
class DownloadJob:
    ticket_id: int
    user_id: int
    url: str
    quality: Quality
    attempts: int = 0


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
        retries: int = 3,
        backoff_seconds: float = 2.0,
        backoff_max: float = 30.0,
        max_temp_bytes: int = 0,
        sleep: Callable[..., Any] = asyncio.sleep,
    ) -> None:
        self._engine = engine
        self._tickets = tickets
        self._policy = policy
        self._temp_dir = Path(temp_dir)
        self._min_free_mb = min_free_mb
        self._retries = max(1, int(retries))
        self._backoff_seconds = max(0.0, float(backoff_seconds))
        self._backoff_max = max(0.0, float(backoff_max))
        self._max_temp_bytes = max(0, int(max_temp_bytes))
        self._sleep = sleep

    @property
    def temp_dir(self) -> Path:
        return self._temp_dir

    @property
    def retries(self) -> int:
        return self._retries

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
            attempt=job.attempts + 1 if job.attempts else 1,
        )
        try:
            asset = await self._fetch_with_retries(job, delivery, on_progress, cancel_event, host)

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
            self._tickets.update(
                job.ticket_id,
                _status("failed"),
                error=str(exc)[:500],
                error_kind=failure_kind_of(exc),
            )
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
            self._tickets.update(
                job.ticket_id,
                _status("failed"),
                error=str(exc)[:500],
                error_kind=FailureKind.UNKNOWN.value,
            )
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

    # --- fetch loop ----------------------------------------------------------
    async def _fetch_with_retries(
        self,
        job: DownloadJob,
        delivery: Delivery,
        on_progress,
        cancel_event: Optional[asyncio.Event],
        host: str,
    ) -> MediaAsset:
        self._guard_disk()
        last_error: Optional[TransientDownloadFailed] = None

        for attempt in range(1, self._retries + 1):
            _check_cancel(cancel_event)
            self._tickets.update(
                job.ticket_id,
                _status("downloading"),
                attempts=job.attempts + attempt,
            )
            try:
                return await self._engine.fetch(
                    job.url,
                    job.ticket_id,
                    job.quality,
                    on_progress=on_progress,
                    cancel_event=cancel_event,
                )
            except TransientDownloadFailed as exc:
                last_error = exc
                pipeline(
                    "job.retry",
                    ticket=job.ticket_id,
                    host=host,
                    attempt=f"{attempt}/{self._retries}",
                    delay_s=self._delay_for(attempt),
                    category=type(exc).__name__,
                    reason=str(exc)[:160],
                    level=logging.WARNING,
                )
                if attempt >= self._retries:
                    break
                self._tickets.update(
                    job.ticket_id,
                    _status("downloading"),
                    error=str(exc)[:500],
                    error_kind=failure_kind_of(exc),
                )
                await self._wait_backoff(self._delay_for(attempt), cancel_event)

        assert last_error is not None
        # Re-raise the *kind* of the last failure (stalled vs transient) so the
        # stored error_kind and the admin panel tell the truth.
        raise type(last_error)(
            f"Gave up after {self._retries} attempts: {last_error}"
        )

    def _delay_for(self, attempt: int) -> float:
        if self._backoff_seconds <= 0:
            return 0.0
        return min(self._backoff_seconds * (2 ** (attempt - 1)), self._backoff_max)

    async def _wait_backoff(self, delay: float, cancel_event: Optional[asyncio.Event]) -> None:
        """Sleep between attempts, but a cancel must cut the wait short."""
        _check_cancel(cancel_event)
        if delay <= 0:
            await asyncio.sleep(0)
            return
        if cancel_event is None:
            await self._sleep(delay)
            return
        sleeper = asyncio.ensure_future(self._sleep(delay))
        waiter = asyncio.ensure_future(cancel_event.wait())
        try:
            await asyncio.wait({sleeper, waiter}, return_when=asyncio.FIRST_COMPLETED)
        finally:
            for task in (sleeper, waiter):
                task.cancel()
        if cancel_event.is_set():
            raise DownloadCancelled()

    # --- internals ----------------------------------------------------------
    def _guard_disk(self) -> None:
        free_mb = shutil.disk_usage(self._temp_dir).free // (1024 * 1024)
        if free_mb < self._min_free_mb:
            raise NotEnoughDisk(
                f"The server is low on free disk space ({free_mb} MB left). "
                "Try again in a little while."
            )
        budget = self._max_temp_bytes
        if budget:
            used = sum(
                entry.stat().st_size
                for entry in self._temp_dir.glob("*")
                if entry.is_file()
            )
            if used > budget:
                raise NotEnoughDisk(
                    "The download folder is busy right now "
                    f"({used // (1024 * 1024)} MB of temp files). Try again shortly."
                )

    # --- internals ----------------------------------------------------------
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
