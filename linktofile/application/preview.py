"""Preview metadata with a cache and a hard deadline.

A media page is probed once, then the card is rebuilt for free from the cache.
The probe is best-effort by design: a slow or broken site must never delay a
download, so a timeout or error degrades to ``None`` and the caller falls back
to the plain quality picker.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Optional

from linktofile.domain.models import MediaInfo
from linktofile.domain.ports import MediaProbe

logger = logging.getLogger(__name__)


class PreviewService:
    def __init__(
        self,
        probe: MediaProbe,
        *,
        timeout: float = 12.0,
        ttl: float = 300.0,
        capacity: int = 2_000,
    ) -> None:
        self._probe = probe
        # timeout <= 0 disables previews entirely (no probe, no delay)
        self._timeout = float(timeout)
        self._ttl = max(0.0, float(ttl))
        self._capacity = max(1, int(capacity))
        self._cache: dict[str, tuple[MediaInfo, float]] = {}

    @property
    def cache_size(self) -> int:
        return len(self._cache)

    async def describe(self, url: str) -> Optional[MediaInfo]:
        """Return metadata for ``url``, or ``None`` when it cannot be read in time."""
        if self._timeout <= 0:
            return None

        cached = self._cache.get(url)
        now = time.monotonic()
        if cached is not None and now - cached[1] < self._ttl:
            return cached[0]

        try:
            info = await asyncio.wait_for(self._probe.probe(url), timeout=self._timeout)
        except asyncio.TimeoutError:
            logger.debug("Preview timed out after %ss for %s", self._timeout, url)
            return None
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - a preview is never a hard failure
            logger.debug("Preview failed for %s: %s", url, exc)
            return None

        if info is None:
            return None

        self._cache[url] = (info, time.monotonic())
        self._prune()
        return info

    def forget(self, url: Optional[str] = None) -> None:
        if url is None:
            self._cache.clear()
        else:
            self._cache.pop(url, None)

    def _prune(self) -> None:
        """Keep the cache bounded: expired entries first, then the oldest."""
        if len(self._cache) <= self._capacity:
            return
        now = time.monotonic()
        for key, (_, stamp) in list(self._cache.items()):
            if now - stamp >= self._ttl:
                self._cache.pop(key, None)
        overflow = len(self._cache) - self._capacity
        if overflow > 0:
            for key in list(self._cache)[:overflow]:
                self._cache.pop(key, None)
