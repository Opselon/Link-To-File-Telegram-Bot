"""Queue recovery: replaying durable rows after a restart.

The application-level contract, not the SQL: workers start, dead jobs are
closed honestly, and every resumable row runs exactly once through the normal
workflow (so retries, delivery and cleanup behave exactly as in a live run).
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest

from linktofile.application.active import ActiveJobs
from linktofile.application.downloads import DownloadWorkflow
from linktofile.application.queue import DownloadQueue
from linktofile.application.recovery import QueueRecovery
from linktofile.domain.models import DownloadStatus, MediaAsset, Quality
from linktofile.domain.policies import FormatPolicy
from linktofile.infrastructure.persistence.sqlite import (
    Database,
    SqliteTickets,
    SqliteUsers,
)

USER = 7


class StubEngine:
    def __init__(self, tmp: Path) -> None:
        self.tmp = tmp
        self.calls: list[int] = []

    async def fetch(self, url, ticket_id, quality, *, on_progress=None, cancel_event=None):
        self.calls.append(ticket_id)
        path = self.tmp / f"{ticket_id}_resumed.bin"
        path.write_bytes(b"payload")
        return MediaAsset(path=path, size=7, title="resumed")


class FakeDelivery:
    def __init__(self) -> None:
        self.sent: list[int] = []
        self.failures: list[str] = []

    async def delivering(self, asset):
        return None

    async def delivered(self, asset):
        self.sent.append(asset.size)

    async def failed(self, error):
        self.failures.append(str(error))

    async def cancelled(self):
        return None


@pytest.fixture
def stack(tmp_path):
    """A fresh 'process': database handle, queue, workflow, active map."""
    database = Database(tmp_path / "bot.db")
    users = SqliteUsers(database)
    users.register(USER)
    tickets = SqliteTickets(database)
    engine = StubEngine(tmp_path)
    workflow = DownloadWorkflow(
        engine=engine,
        tickets=tickets,
        policy=FormatPolicy(cap_bytes=1024 * 1024),
        temp_dir=tmp_path,
        min_free_mb=0,
        retries=2,
        backoff_seconds=0,
    )
    queue = DownloadQueue(concurrency=2, per_user_limit=5, max_size=20, task_timeout=10)
    return SimpleNamespace(
        database=database,
        users=users,
        tickets=tickets,
        engine=engine,
        workflow=workflow,
        queue=queue,
        active=ActiveJobs(),
        deliveries=[],
        tmp=tmp_path,
    )


def _recovery(stack, *, max_jobs: int = 50) -> QueueRecovery:
    def factory(job):
        delivery = FakeDelivery()
        stack.deliveries.append(delivery)
        return delivery

    return QueueRecovery(
        tickets=stack.tickets,
        queue=stack.queue,
        workflow=stack.workflow,
        active=stack.active,
        delivery_factory=factory,
        max_jobs=max_jobs,
    )


async def test_a_queued_job_is_resumed_and_delivered(stack):
    ticket = stack.tickets.create(USER, "https://example.com/a.bin")
    stack.tickets.enqueue(ticket.id, Quality.video(720))
    recovery = _recovery(stack)
    stack.queue.start()
    try:
        report = await recovery.resume()
        await stack.queue.drain(timeout=10)
    finally:
        await stack.queue.stop()

    assert report.resumed == 1
    assert stack.engine.calls == [ticket.id]
    assert stack.tickets.get(ticket.id).status is DownloadStatus.COMPLETED
    assert stack.deliveries[0].sent == [7]
    assert stack.active.all() == []


async def test_interrupted_and_abandoned_jobs_are_failed_not_resumed(stack):
    running = stack.tickets.create(USER, "https://example.com/running.bin")
    stack.tickets.enqueue(running.id, Quality.video(720))
    stack.tickets.update(running.id, DownloadStatus.DOWNLOADING)
    abandoned = stack.tickets.create(USER, "https://example.com/never.bin")

    recovery = _recovery(stack)
    report = await recovery.resume()

    assert report.resumed == 0
    assert report.interrupted == 1
    assert report.abandoned == 1
    assert stack.tickets.get(running.id).status is DownloadStatus.FAILED
    assert stack.tickets.get(abandoned.id).status is DownloadStatus.FAILED
    assert stack.engine.calls == []
    assert stack.queue.pending == 0


async def test_the_resume_cap_defers_the_rest(stack):
    for index in range(3):
        ticket = stack.tickets.create(USER, f"https://example.com/{index}.bin")
        stack.tickets.enqueue(ticket.id, Quality.video(720))

    recovery = _recovery(stack, max_jobs=2)

    report = await recovery.resume()

    assert report.resumed == 3  # what recovery found
    assert len(stack.queue.keys()) == 2  # what was submitted


async def test_a_job_already_in_memory_is_not_submitted_twice(stack):
    ticket = stack.tickets.create(USER, "https://example.com/dup.bin")
    stack.tickets.enqueue(ticket.id, Quality.video(720))

    blocker = asyncio.Event()

    async def stuck():
        await blocker.wait()

    stack.queue.start()
    try:
        await stack.queue.submit(stuck, key=ticket.id, user_id=USER)
        stack.tickets.update(ticket.id, DownloadStatus.DOWNLOADING)
        recovery = _recovery(stack)
        await recovery.resume()
        blocker.set()
        await stack.queue.drain(timeout=10)
    finally:
        await stack.queue.stop()

    assert stack.engine.calls == []


async def test_resume_is_safe_to_call_twice(stack):
    ticket = stack.tickets.create(USER, "https://example.com/once.bin")
    stack.tickets.enqueue(ticket.id, Quality.video(720))
    recovery = _recovery(stack)
    stack.queue.start()
    try:
        await recovery.resume()
        await recovery.resume()
        await stack.queue.drain(timeout=10)
    finally:
        await stack.queue.stop()

    assert stack.engine.calls == [ticket.id]
    assert stack.tickets.get(ticket.id).status is DownloadStatus.COMPLETED
