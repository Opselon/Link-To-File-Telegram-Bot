"""A tiny bounded work queue with N parallel workers.

Downloads are I/O bound, so a handful of workers is what makes the bot feel
fast when several people use it at once. Each job also gets a *real* task
handle, so ``cancel()`` interrupts it immediately instead of waiting for a
progress callback that may never come on a stalled socket.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from typing import Any, Callable, Coroutine, Optional

from config import QUEUE_MAX_SIZE, QUEUE_TASK_TIMEOUT

logger = logging.getLogger(__name__)


class QueueFull(Exception):
    """Raised when too many jobs are waiting; the caller should tell the user."""


@dataclass(slots=True)
class _Job:
    func: Callable[..., Coroutine[Any, Any, Any]]
    args: tuple = ()
    kwargs: dict = field(default_factory=dict)
    key: Optional[int] = None


class DownloadQueue:
    def __init__(self, concurrency: int = 1, max_size: int = QUEUE_MAX_SIZE):
        self.queue: asyncio.Queue = asyncio.Queue(maxsize=max_size)
        self.concurrency = max(1, int(concurrency))
        self.workers: list[asyncio.Task] = []
        self._running = False
        self.active = 0
        # key -> running task, plus the keys whose cancellation was requested
        self._tasks: dict[int, asyncio.Task] = {}
        self._cancelled: set[int] = set()

    @property
    def pending(self) -> int:
        return self.queue.qsize()

    def is_running(self, key: int) -> bool:
        return key in self._tasks

    async def add_task(
        self,
        coro_func: Callable[..., Coroutine[Any, Any, Any]],
        *args: Any,
        job_key: Optional[int] = None,
        **kwargs: Any,
    ) -> None:
        job = _Job(func=coro_func, args=args, kwargs=kwargs, key=job_key)
        try:
            self.queue.put_nowait(job)
        except asyncio.QueueFull as exc:
            raise QueueFull from exc
        logger.info("Task queued (waiting=%s, active=%s)", self.pending, self.active)

    def cancel(self, key: int) -> bool:
        """Cancel a queued or running job. Safe to call more than once."""
        self._cancelled.add(key)
        task = self._tasks.get(key)
        if task is not None and not task.done():
            task.cancel()
            logger.info("Cancelled running job %s", key)
            return True
        queued = any(item.key == key for item in list(self.queue._queue))  # type: ignore[attr-defined]
        if queued:
            # It aborts the moment a worker picks it up (its cancel event is set).
            logger.info("Marked queued job %s as cancelled", key)
            return True
        return False

    async def _worker(self) -> None:
        while self._running:
            job: _Job = await self.queue.get()
            self.active += 1
            task: Optional[asyncio.Task] = None
            try:
                task = asyncio.create_task(job.func(*job.args, **job.kwargs))
                if job.key is not None:
                    self._tasks[job.key] = task
                await asyncio.wait_for(task, timeout=QUEUE_TASK_TIMEOUT)
            except asyncio.CancelledError:
                # Distinguish "the user cancelled this job" from "the bot is
                # shutting down" — only the second one must kill the worker.
                if self._running and job.key is not None and job.key in self._cancelled:
                    logger.info("Job %s cancelled by user", job.key)
                else:
                    raise
            except asyncio.TimeoutError:
                logger.error("Task timed out after %ss", QUEUE_TASK_TIMEOUT)
            except Exception as exc:  # noqa: BLE001 - one bad job must not kill a worker
                logger.exception("Error in worker task: %s", exc)
            finally:
                if job.key is not None:
                    self._tasks.pop(job.key, None)
                    self._cancelled.discard(job.key)
                self.active = max(0, self.active - 1)
                self.queue.task_done()
                logger.info(
                    "Task finished (waiting=%s, active=%s)", self.pending, self.active
                )

    def start(self) -> None:
        if self._running:
            return
        self._running = True
        for index in range(self.concurrency):
            self.workers.append(asyncio.create_task(self._worker(), name=f"worker-{index}"))
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
