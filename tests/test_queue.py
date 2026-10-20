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
