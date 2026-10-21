"""Adapter: exposes the hardened download engine through the domain port."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Optional

from linktofile.domain.models import MediaAsset, Quality
from linktofile.domain.policies import FormatPolicy
from linktofile.infrastructure.download.engine import DownloadError, download_file


class EngineDownloadAdapter:
    """Translates a domain ``Quality`` into engine options and back."""

    def __init__(self, policy: FormatPolicy) -> None:
        self._policy = policy

    async def fetch(
        self,
        url: str,
        request_id: int,
        quality: Quality,
        *,
        on_progress=None,
        cancel_event: Optional[asyncio.Event] = None,
    ) -> MediaAsset:
        result = await download_file(
            url,
            request_id,
            self._policy.options(quality),
            progress_callback=on_progress,
            cancel_event=cancel_event,
        )
        return MediaAsset(
            path=Path(result.path),
            size=int(result.size),
            title=result.title or Path(result.path).stem,
            duration=result.duration,
            width=result.width,
            height=result.height,
            artist=result.artist,
            source=result.source,
        )

    async def close(self) -> None:
        from linktofile.infrastructure.download.engine import close_session

        await close_session()


__all__ = ["EngineDownloadAdapter", "DownloadError"]
