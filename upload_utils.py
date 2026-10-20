"""Streaming file source that reports upload progress back to the chat card."""

from __future__ import annotations

import asyncio
import os
import time
from typing import AsyncGenerator, Callable, Optional

import aiofiles
from aiogram.client.bot import Bot
from aiogram.types import FSInputFile

from config import UPLOAD_CHUNK_SIZE


class ProgressFSInputFile(FSInputFile):
    """FSInputFile that emits ``(sent, total, speed, eta)`` while uploading."""

    def __init__(
        self,
        path: str,
        filename: Optional[str] = None,
        chunk_size: int = UPLOAD_CHUNK_SIZE,
        progress_callback: Optional[Callable[[int, int, float, float], None]] = None,
    ):
        super().__init__(path=path, filename=filename, chunk_size=chunk_size)
        self.total_size = os.path.getsize(path)
        self.progress_callback = progress_callback
        self.sent_bytes = 0
        self.start_time: Optional[float] = None

    async def read(self, bot: Bot) -> AsyncGenerator[bytes, None]:
        if self.start_time is None:
            self.start_time = time.monotonic()

        async with aiofiles.open(self.path, "rb") as file:
            while chunk := await file.read(self.chunk_size):
                self.sent_bytes += len(chunk)

                if self.progress_callback:
                    elapsed = max(time.monotonic() - self.start_time, 1e-6)
                    speed = self.sent_bytes / elapsed
                    remaining = max(self.total_size - self.sent_bytes, 0)
                    eta = remaining / speed if speed > 0 else 0
                    result = self.progress_callback(
                        self.sent_bytes, self.total_size, speed, eta
                    )
                    if result is not None and asyncio.iscoroutine(result):
                        await result

                yield chunk
