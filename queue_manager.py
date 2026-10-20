"""A tiny bounded work queue with N parallel workers.

Downloads are I/O bound, so a handful of workers is what makes the bot feel
fast when several people use it at once.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Callable, Coroutine

from config import QUEUE_MAX_SIZE, QUEUE_TASK_TIMEOUT

logger = logging.getLogger(__name__)


class QueueFull(Exception):
    """Raised when too many jobs are waiting; the caller should tell the user."""


class DownloadQueue:
    def __init__(self, concurrency: int = 1, max_size: int = QUEUE_MAX_SIZE):
        self.queue: asyncio.Queue = asyncio.Queue(maxsize=max_size)
        self.concurrency = max(1, int(concurrency))
        self.workers: list[asyncio.Task] = []
        self._running = False
        self.active = 0

    @property
    def pending(self) -> int:
        return self.queue.qsize()

    async def add_task(
        self,
        coro_func: Callable[..., Coroutine[Any, Any, Any]],
        *args: Any,
        **kwargs: Any,
    ) -> None:
        try:
            self.queue.put_nowait((coro_func, args, kwargs))
        except asyncio.QueueFull as exc:
            raise QueueFull from exc
        logger.info("Task queued (waiting=%s, active=%s)", self.pending, self.active)

    async def _worker(self) -> None:
        while self._running:
            coro_func, args, kwargs = await self.queue.get()
            self.active += 1
            try:
                await asyncio.wait_for(
                    coro_func(*args, **kwargs), timeout=QUEUE_TASK_TIMEOUT
                )
            except asyncio.TimeoutError:
                logger.error("Task timed out after %ss", QUEUE_TASK_TIMEOUT)
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 - one bad job must not kill a worker
                logger.exception("Error in worker task: %s", exc)
            finally:
                self.active = max(0, self.active - 1)
                self.queue.task_done()
                logger.info(
                    "Task finished (waiting=%s, active=%s)", self.pending, self.active
                )

    def start(self) -> None:
        if self._running:
            return
        self._running = True
        for _ in range(self.concurrency):
            self.workers.append(asyncio.create_task(self._worker()))
        logger.info("Started %s queue worker(s).", self.concurrency)

    async def stop(self) -> None:
        self._running = False
        for worker in self.workers:
            worker.cancel()
        await asyncio.gather(*self.workers, return_exceptions=True)
        self.workers = []
        logger.info("Stopped queue workers.")

    async def drain(self, timeout: float = 30.0) -> None:
        """Best-effort wait for in-flight jobs on shutdown."""
        try:
            await asyncio.wait_for(self.queue.join(), timeout=timeout)
        except (asyncio.TimeoutError, asyncio.CancelledError):
            pass


download_queue = DownloadQueue(concurrency=3)
