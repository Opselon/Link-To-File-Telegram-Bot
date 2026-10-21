import asyncio

import pytest

from queue_manager import DownloadQueue, QueueFull


@pytest.mark.asyncio
async def test_queue_processing():
    queue = DownloadQueue(concurrency=1)
    queue.start()

    results = []

    async def task(value):
        await asyncio.sleep(0.05)
        results.append(value)

    await queue.add_task(task, 1)
    await queue.add_task(task, 2)
    await queue.queue.join()

    assert results == [1, 2]
    await queue.stop()


@pytest.mark.asyncio
async def test_queue_runs_jobs_in_parallel():
    queue = DownloadQueue(concurrency=3)
    queue.start()

    running = 0
    peak = 0

    async def task():
        nonlocal running, peak
        running += 1
        peak = max(peak, running)
        await asyncio.sleep(0.15)
        running -= 1

    for _ in range(3):
        await queue.add_task(task)
    await queue.queue.join()
    await queue.stop()

    assert peak == 3


@pytest.mark.asyncio
async def test_queue_is_bounded():
    queue = DownloadQueue(concurrency=1, max_size=1)  # no workers: nothing drains

    async def slow():
        await asyncio.sleep(0.2)

    await queue.add_task(slow)
    with pytest.raises(QueueFull):
        await queue.add_task(slow)


@pytest.mark.asyncio
async def test_timeout_does_not_kill_the_worker(monkeypatch):
    import queue_manager

    monkeypatch.setattr(queue_manager, "QUEUE_TASK_TIMEOUT", 0.05)
    queue = DownloadQueue(concurrency=1)
    queue.start()

    async def slow_task():
        await asyncio.sleep(2)

    done = []

    async def quick_task():
        done.append(True)

    await queue.add_task(slow_task)
    await queue.add_task(quick_task)
    await asyncio.wait_for(queue.queue.join(), timeout=5)
    await queue.stop()

    assert done == [True]


@pytest.mark.asyncio
async def test_stop_cancels_workers():
    queue = DownloadQueue(concurrency=2)
    queue.start()
    assert len(queue.workers) == 2
    await queue.stop()
    assert queue.workers == []


@pytest.mark.asyncio
async def test_cancel_interrupts_a_blocked_job():
    """A job stuck on I/O must stop at once, not at the next progress tick."""
    import asyncio

    from queue_manager import DownloadQueue

    queue = DownloadQueue(concurrency=1)
    queue.start()
    started = asyncio.Event()
    cleaned = asyncio.Event()

    async def stuck_job():
        started.set()
        try:
            await asyncio.sleep(3600)      # stands in for a stalled socket
        finally:
            cleaned.set()

    try:
        await queue.add_task(stuck_job, job_key=99)
        await asyncio.wait_for(started.wait(), timeout=2)
        assert queue.cancel(99) is True
        await asyncio.wait_for(cleaned.wait(), timeout=2)
        # the worker survived and can take the next job
        ran = asyncio.Event()

        async def quick():
            ran.set()

        await queue.add_task(quick, job_key=100)
        await asyncio.wait_for(ran.wait(), timeout=2)
    finally:
        await queue.stop()


@pytest.mark.asyncio
async def test_cancel_marks_queued_job_before_it_starts():
    import asyncio

    from queue_manager import DownloadQueue

    queue = DownloadQueue(concurrency=1)   # not started: nothing runs yet

    async def job():
        await asyncio.sleep(0)

    await queue.add_task(job, job_key=7)
    assert queue.cancel(7) is True
    assert queue.cancel(12345) is False
