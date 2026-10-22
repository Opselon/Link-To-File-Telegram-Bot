"""Restart recovery: rebuild the queue from the durable rows.

The workflow marks a job ``queued`` (with its quality) *before* it reaches the
in-memory asyncio queue, so a crash leaves a committed record rather than a lost
request. On boot this service replays those rows through the normal workflow.

What it deliberately does **not** do:

* resume a ticket that never got a quality (status ``pending``) — the intent is
  unknowable, so it is failed as ``abandoned``;
* resume a ``downloading``/``uploading`` ticket — its temp file is gone, so it
  is failed as ``interrupted`` instead of pretending it is still running.
"""

from __future__ import annotations

import logging
from typing import Callable, Optional, Protocol

from linktofile.application.active import ActiveJobs
from linktofile.application.downloads import Delivery, DownloadJob, DownloadWorkflow
from linktofile.application.queue import DownloadQueue
from linktofile.domain.errors import DuplicateJob, QueueSaturated
from linktofile.domain.models import FailureKind, Quality, RecoveryReport, ResumableJob
from linktofile.domain.ports import TicketRepository
from linktofile.shared.telemetry import pipeline

logger = logging.getLogger(__name__)

DeliveryFactory = Callable[[ResumableJob], Delivery]


class Recovery(Protocol):
    async def resume(self) -> RecoveryReport: ...


class QueueRecovery:
    """Replays the durable queue once, right after the workers start."""

    def __init__(
        self,
        *,
        tickets: TicketRepository,
        queue: DownloadQueue,
        workflow: DownloadWorkflow,
        active: ActiveJobs,
        delivery_factory: DeliveryFactory,
        max_jobs: int = 50,
    ) -> None:
        self._tickets = tickets
        self._queue = queue
        self._workflow = workflow
        self._active = active
        self._delivery_factory = delivery_factory
        self._max_jobs = max(0, int(max_jobs))
        self.last_report: Optional[RecoveryReport] = None

    async def resume(self) -> RecoveryReport:
        report = self._tickets.recover_after_restart()
        self.last_report = report
        resumed = 0

        for item in report.resumable[: self._max_jobs]:
            if self._queue.is_running(item.ticket_id) or self._queue.is_queued(item.ticket_id):
                continue
            job = DownloadJob(
                ticket_id=item.ticket_id,
                user_id=item.user_id,
                url=item.url,
                quality=item.quality or Quality.video(None),
                attempts=item.attempts,
            )
            delivery = self._delivery_factory(item)
            event = self._active.open(item.user_id, item.ticket_id)

            async def run(
                job: DownloadJob = job,
                delivery: Delivery = delivery,
                event=event,
            ) -> None:
                try:
                    await self._workflow.run(job, delivery, cancel_event=event)
                finally:
                    self._active.close(job.ticket_id)

            try:
                await self._queue.submit(
                    run, key=item.ticket_id, user_id=item.user_id
                )
            except (QueueSaturated, DuplicateJob) as exc:
                self._active.close(item.ticket_id)
                self._tickets.fail(
                    item.ticket_id,
                    kind=FailureKind.QUEUE_FULL,
                    error=f"Could not be resumed after restart: {exc}",
                )
                pipeline(
                    "queue.resume_failed",
                    ticket=item.ticket_id,
                    reason=type(exc).__name__,
                    level=logging.WARNING,
                )
                continue
            resumed += 1
            pipeline("queue.resume", ticket=item.ticket_id, host=item.url, quality=item.quality.label if item.quality else "")

        deferred = max(0, len(report.resumable) - self._max_jobs)
        pipeline(
            "queue.recovered",
            found=report.resumed,
            resumed=resumed,
            deferred=deferred,
            interrupted=report.interrupted,
            abandoned=report.abandoned,
        )
        if report.resumed or report.interrupted or report.abandoned:
            logger.info(
                "Recovery: %s resumed, %s interrupted, %s abandoned",
                resumed,
                report.interrupted,
                report.abandoned,
            )
        return report


__all__ = ["DeliveryFactory", "QueueRecovery", "Recovery"]
