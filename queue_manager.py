import asyncio
import logging
from typing import Callable, Coroutine, Any
from config import QUEUE_TASK_TIMEOUT

logger = logging.getLogger(__name__)

class DownloadQueue:
    def __init__(self, concurrency: int = 1):
        self.queue = asyncio.Queue()
        self.concurrency = concurrency
        self.workers = []
        self._running = False

    async def add_task(self, coro_func: Callable[..., Coroutine[Any, Any, Any]], *args, **kwargs):
        await self.queue.put((coro_func, args, kwargs))
        logger.info(f"Task added to queue. Queue size: {self.queue.qsize()}")

    async def _worker(self):
        while self._running:
            coro_func, args, kwargs = await self.queue.get()
            try:
                logger.info(f"Processing task")
                # Wrap the task in a timeout to prevent hanging forever
                await asyncio.wait_for(coro_func(*args, **kwargs), timeout=QUEUE_TASK_TIMEOUT)
            except asyncio.TimeoutError:
                logger.error(f"Task timed out after {QUEUE_TASK_TIMEOUT} seconds")
                # We try to notify the user if possible, but execute_download
                # should ideally handle its own internal timeouts too.
            except Exception as e:
                logger.exception(f"Error in worker task: {e}")
            finally:
                self.queue.task_done()
                logger.info(f"Task completed. Remaining in queue: {self.queue.qsize()}")

    def start(self):
        if self._running:
            return
        self._running = True
        for _ in range(self.concurrency):
            worker = asyncio.create_task(self._worker())
            self.workers.append(worker)
        logger.info(f"Started {self.concurrency} queue workers.")

    async def stop(self):
        self._running = False
        for worker in self.workers:
            worker.cancel()
        await asyncio.gather(*self.workers, return_exceptions=True)
        self.workers = []
        logger.info("Stopped queue workers.")

download_queue = DownloadQueue(concurrency=1)
