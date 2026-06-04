import pytest
import asyncio
from queue_manager import DownloadQueue

async def mock_coro(val):
    await asyncio.sleep(0.1)
    return val

@pytest.mark.asyncio
async def test_queue_processing():
    queue = DownloadQueue(concurrency=1)
    queue.start()

    results = []
    async def task(val):
        await asyncio.sleep(0.05)
        results.append(val)

    await queue.add_task(task, 1)
    await queue.add_task(task, 2)

    # Wait for tasks to complete
    await queue.queue.join()

    assert results == [1, 2]
    await queue.stop()

@pytest.mark.asyncio
async def test_queue_timeout():
    queue = DownloadQueue(concurrency=1)
    queue.start()

    async def slow_task():
        await asyncio.sleep(2)

    # We'll monkeypatch the timeout for this test
    import queue_manager
    from unittest.mock import patch

    with patch('asyncio.wait_for', side_effect=asyncio.TimeoutError):
        await queue.add_task(slow_task)
        await queue.queue.join()
        # If it reaches here without hanging, it's good.
        # The error is logged but doesn't crash the worker.

    await queue.stop()
