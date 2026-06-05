import os
import time
import asyncio
from typing import AsyncGenerator, Optional, Callable
import aiofiles
from aiogram.types import FSInputFile
from aiogram.client.bot import Bot

class ProgressFSInputFile(FSInputFile):
    def __init__(
        self,
        path: str,
        filename: Optional[str] = None,
        chunk_size: int = 1024 * 1024,
        progress_callback: Optional[Callable[[int, int, float, float], None]] = None
    ):
        super().__init__(path=path, filename=filename, chunk_size=chunk_size)
        self.total_size = os.path.getsize(path)
        self.progress_callback = progress_callback
        self.downloaded_bytes = 0
        self.start_time = None

    async def read(self, bot: Bot) -> AsyncGenerator[bytes, None]:
        if self.start_time is None:
            self.start_time = time.time()

        async with aiofiles.open(self.path, "rb") as f:
            while chunk := await f.read(self.chunk_size):
                self.downloaded_bytes += len(chunk)

                if self.progress_callback:
                    current_time = time.time()
                    elapsed = current_time - self.start_time
                    speed = self.downloaded_bytes / elapsed if elapsed > 0 else 0
                    eta = (self.total_size - self.downloaded_bytes) / speed if speed > 0 else 0

                    if asyncio.iscoroutinefunction(self.progress_callback):
                        await self.progress_callback(self.downloaded_bytes, self.total_size, speed, eta)
                    else:
                        self.progress_callback(self.downloaded_bytes, self.total_size, speed, eta)

                yield chunk
