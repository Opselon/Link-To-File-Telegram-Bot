"""Queue: bounded, fair, and cancellable — including while blocked on I/O."""

import asyncio

import pytest

from linktofile.application.queue import DownloadQueue
from linktofile.domain.errors import QueueSaturated


async def test_jobs_run_in_order():
    queue = DownloadQueue(concurrency=1)
    queue.start()
    seen: list[int] = []

    async def job(value):
        await asyncio.sleep(0.01)
        seen.append(value)

    await queue.submit(job, 1)
    await queue.submit(job, 2)
    await queue.drain(timeout=5)
    assert seen == [1, 2]
    await queue.stop()


async def test_queue_is_bounded():
    queue = DownloadQueue(concurrency=1, max_size=1)
    # Not started: nothing drains, so the second submit must be refused.

    async def job():
        return None

    await queue.submit(job)
    with pytest.raises(QueueSaturated):
        await queue.submit(job)


async def test_per_user_limit_refuses_the_third_job():
    queue = DownloadQueue(concurrency=1, per_user_limit=2, max_size=10)

    async def job():
        return None

    await queue.submit(job, user_id=5)
    await queue.submit(job, user_id=5)
    with pytest.raises(QueueSaturated):
        await queue.submit(job, user_id=5)
    # Another user is unaffected.
    await queue.submit(job, user_id=6)


async def test_snapshot_reports_load():
    queue = DownloadQueue(concurrency=1)
    queue.start()
    blocker = asyncio.Event()
    started = asyncio.Event()

    async def slow():
        started.set()
        await blocker.wait()

    await queue.submit(slow, user_id=9, key=1)
    await asyncio.wait_for(started.wait(), 2)
    snapshot = queue.snapshot()
    assert snapshot.active == 1
    assert queue.load(9) == 1
    assert queue.is_running(1)

    blocker.set()
    await queue.drain(timeout=5)
    assert queue.load(9) == 0
    await queue.stop()


async def test_cancel_interrupts_a_blocked_job():
    queue = DownloadQueue(concurrency=1)
    queue.start()
    started = asyncio.Event()
    finished = asyncio.Event()

    async def stuck():
        started.set()
        try:
            await asyncio.sleep(30)  # stands in for a hung socket
        finally:
            finished.set()

    await queue.submit(stuck, key=42)
    await asyncio.wait_for(started.wait(), 2)

    assert queue.cancel(42) is True
    await asyncio.wait_for(finished.wait(), 2)
    await queue.drain(timeout=5)
    assert not queue.is_running(42)
    await queue.stop()


async def test_cancel_while_queued_skips_execution():
    queue = DownloadQueue(concurrency=1)
    queue.start()
    ran: list[str] = []
    blocker = asyncio.Event()
    started = asyncio.Event()

    async def first():
        started.set()
        await blocker.wait()

    async def second():
        ran.append("second")

    await queue.submit(first, key=1)
    await asyncio.wait_for(started.wait(), 2)
    await queue.submit(second, key=2)

    assert queue.cancel(2) is True
    blocker.set()
    await queue.drain(timeout=5)

    assert ran == []
    await queue.stop()


async def test_a_failing_job_does_not_kill_the_worker():
    queue = DownloadQueue(concurrency=1)
    queue.start()
    done: list[int] = []

    async def boom():
        raise RuntimeError("upstream broke")

    async def ok():
        done.append(1)

    await queue.submit(boom)
    await queue.submit(ok)
    await queue.drain(timeout=5)
    assert done == [1]
    await queue.stop()
