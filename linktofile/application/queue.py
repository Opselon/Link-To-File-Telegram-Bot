"""Bounded, fair work queue for downloads.

One queue, N workers, one *real* asyncio task per job so ``cancel()`` can
interrupt a stalled socket instead of waiting for the next progress tick.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any, Callable, Coroutine, Optional

from linktofile.domain.errors import DuplicateJob, QueueSaturated
from linktofile.domain.models import QueueSnapshot
from linktofile.shared.telemetry import pipeline

logger = logging.getLogger(__name__)

Job = Callable[..., Coroutine[Any, Any, Any]]


@dataclass(slots=True)
class _Item:
    func: Job
    args: tuple = ()
    kwargs: dict = field(default_factory=dict)
    key: Optional[int] = None
    user_id: Optional[int] = None
    queued_at: float = field(default_factory=time.monotonic)


class DownloadQueue:
    def __init__(
        self,
        *,
        concurrency: int = 3,
        max_size: int = 200,
        per_user_limit: int = 2,
        task_timeout: float = 1800,
        on_failure: Optional[Callable[[int, str, str], None]] = None,
    ) -> None:
        self._queue: asyncio.Queue[_Item] = asyncio.Queue(maxsize=max_size)
        self.concurrency = max(1, int(concurrency))
        self.per_user_limit = max(1, int(per_user_limit))
        self.task_timeout = float(task_timeout)
        self._on_failure = on_failure
        self._workers: list[asyncio.Task] = []
        self._running = False
        self._active = 0
        self._tasks: dict[int, asyncio.Task] = {}
        self._cancelled: set[int] = set()
        self._per_user: dict[int, int] = defaultdict(int)

    # --- introspection ------------------------------------------------------
    @property
    def pending(self) -> int:
        return self._queue.qsize()

    @property
    def active(self) -> int:
        return self._active

    def snapshot(self) -> QueueSnapshot:
        return QueueSnapshot(active=self._active, pending=self.pending)

    def load(self, user_id: int) -> int:
        return self._per_user.get(user_id, 0)

    def is_running(self, key: int) -> bool:
        return key in self._tasks

    def is_queued(self, key: int) -> bool:
        return any(item.key == key for item in list(self._queue._queue))  # type: ignore[attr-defined]

    def keys(self) -> dict[int, str]:
        """Live tickets and where they sit — the admin panel's queue view."""
        view: dict[int, str] = {}
        for item in list(self._queue._queue):  # type: ignore[attr-defined]
            if item.key is not None:
                view[int(item.key)] = "queued"
        for key in self._tasks:
            view[int(key)] = "running"
        return view

    def live_phases(self) -> dict[int, str]:
        """Alias kept explicit for the admin dashboard."""
        return self.keys()

    # --- producer -----------------------------------------------------------
    async def submit(
        self,
        func: Job,
        *args,
        key: Optional[int] = None,
        user_id: Optional[int] = None,
        **kwargs,
    ) -> int:
        """Enqueue a coroutine function; returns its queue position (0 = next).

        Submitting a ticket that is already live is refused: one ticket must
        never run twice (double-tap, a retried callback, or a resumed job that
        is already active).
        """
        if key is not None and (self.is_running(key) or self.is_queued(key)):
            raise DuplicateJob("This job is already in the queue.")
        if user_id is not None and self.load(user_id) >= self.per_user_limit:
            raise QueueSaturated

        try:
            self._queue.put_nowait(
                _Item(func=func, args=args, kwargs=kwargs, key=key, user_id=user_id)
            )
        except asyncio.QueueFull as exc:
            raise QueueSaturated from exc

        if user_id is not None:
            self._per_user[user_id] += 1
        pipeline(
            "job.queued",
            ticket=key,
            user=user_id,
            waiting=self.pending,
            active=self._active,
        )
        return max(self.pending - 1, 0)

    def cancel(self, key: int) -> bool:
        """Cancel a queued or running job. Safe to call more than once."""
        self._cancelled.add(key)
        task = self._tasks.get(key)
        if task is not None and not task.done():
            task.cancel()
            logger.info("Cancelled running job %s", key)
            return True
        queued = any(item.key == key for item in list(self._queue._queue))  # type: ignore[attr-defined]
        if queued:
            logger.info("Marked queued job %s as cancelled", key)
            return True
        return False

    # --- consumer -----------------------------------------------------------
    async def _worker(self, index: int) -> None:
        while self._running:
            item = await self._queue.get()
            self._active += 1
            started = time.monotonic()
            pipeline(
                "job.dequeue",
                ticket=item.key,
                user=item.user_id,
                wait_ms=int((started - item.queued_at) * 1000),
                active=self._active,
            )
            task: Optional[asyncio.Task] = None
            skipped = item.key is not None and item.key in self._cancelled
            try:
                if skipped:
                    logger.info("Skipping job %s: cancelled while queued", item.key)
                    continue
                task = asyncio.create_task(item.func(*item.args, **item.kwargs))
                if item.key is not None:
                    self._tasks[item.key] = task
                await asyncio.wait_for(task, timeout=self.task_timeout)
            except asyncio.CancelledError:
                if self._running and item.key is not None and item.key in self._cancelled:
                    logger.info("Job %s cancelled by user", item.key)
                else:
                    raise
            except asyncio.TimeoutError:
                logger.error("Job %s timed out after %ss", item.key, self.task_timeout)
                pipeline(
                    "job.timeout",
                    ticket=item.key,
                    timeout_s=self.task_timeout,
                    level=logging.ERROR,
                )
                if self._on_failure is not None and item.key is not None:
                    try:
                        self._on_failure(
                            int(item.key),
                            "timeout",
                            f"No progress in {int(self.task_timeout)}s.",
                        )
                    except Exception:  # noqa: BLE001 - the queue must not crash on accounting
                        logger.exception("Timeout accounting failed for job %s", item.key)
            except Exception as exc:  # noqa: BLE001 - one bad job must not kill a worker
                logger.exception("Worker %s job failed: %s", index, exc)
            finally:
                if item.key is not None:
                    self._tasks.pop(item.key, None)
                    self._cancelled.discard(item.key)
                if item.user_id is not None:
                    self._per_user[item.user_id] = max(0, self._per_user[item.user_id] - 1)
                    if not self._per_user[item.user_id]:
                        self._per_user.pop(item.user_id, None)
                self._active = max(0, self._active - 1)
                pipeline(
                    "job.release",
                    ticket=item.key,
                    worker=index,
                    total_ms=int((time.monotonic() - started) * 1000),
                    active=self._active,
                )
                self._queue.task_done()

    # --- lifecycle ----------------------------------------------------------
    def start(self) -> None:
        if self._running:
            return
        self._running = True
        for index in range(self.concurrency):
            self._workers.append(asyncio.create_task(self._worker(index), name=f"worker-{index}"))
        logger.info("Started %s download worker(s)", self.concurrency)

    async def stop(self) -> None:
        self._running = False
        for worker in self._workers:
            worker.cancel()
        await asyncio.gather(*self._workers, return_exceptions=True)
        self._workers.clear()
        logger.info("Stopped download workers")

    async def drain(self, timeout: float = 20.0) -> None:
        try:
            await asyncio.wait_for(self._queue.join(), timeout=timeout)
        except (asyncio.TimeoutError, asyncio.CancelledError):
            pass
