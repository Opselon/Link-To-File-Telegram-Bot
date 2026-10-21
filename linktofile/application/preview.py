"""Preview metadata with a cache, a fast budget and a hard deadline.

A media page is probed once, then the card is rebuilt for free from the cache.
A probe is shared between callers and never cancelled by a short wait: if the
site is slow the picker appears right away and the probe keeps running in the
background, so the next link that needs it is instant.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Optional
from urllib.parse import urlparse

from linktofile.domain.models import MediaInfo
from linktofile.domain.ports import MediaProbe
from linktofile.shared.telemetry import Timer, pipeline

logger = logging.getLogger(__name__)


def _host(url: str) -> str:
    try:
        return (urlparse(url).hostname or "").lower()
    except ValueError:  # pragma: no cover - defensive
        return ""


class PreviewService:
    def __init__(
        self,
        probe: MediaProbe,
        *,
        timeout: float = 12.0,
        wait: float = 5.0,
        ttl: float = 300.0,
        capacity: int = 2_000,
    ) -> None:
        self._probe = probe
        # timeout <= 0 disables previews entirely (no probe, no delay)
        self._timeout = float(timeout)
        self._wait = max(0.0, float(wait))
        self._ttl = max(0.0, float(ttl))
        self._capacity = max(1, int(capacity))
        self._cache: dict[str, tuple[MediaInfo, float]] = {}
        self._inflight: dict[str, asyncio.Task] = {}

    # --- introspection ------------------------------------------------------
    @property
    def cache_size(self) -> int:
        return len(self._cache)

    @property
    def inflight(self) -> int:
        return len(self._inflight)

    def cached(self, url: str) -> Optional[MediaInfo]:
        entry = self._cache.get(url)
        if entry is None:
            return None
        info, stamp = entry
        if time.monotonic() - stamp >= self._ttl:
            self._cache.pop(url, None)
            return None
        return info

    # --- public API ---------------------------------------------------------
    async def describe(self, url: str, *, wait: Optional[float] = None) -> Optional[MediaInfo]:
        """Metadata for ``url`` if it can be read within the wait budget.

        ``None`` means "render the plain picker"; the probe may still be
        running and will fill the cache for the next request.
        """
        if self._timeout <= 0:
            return None

        hit = self.cached(url)
        if hit is not None:
            return hit

        task = self._inflight.get(url)
        if task is None or task.done():
            task = asyncio.create_task(self._fetch(url), name="preview-fetch")
            self._inflight[url] = task

        budget = self._wait if wait is None else max(0.0, float(wait))
        try:
            # shield: a short wait must not cancel the probe that warms the cache
            await asyncio.wait_for(asyncio.shield(task), timeout=budget)
        except asyncio.TimeoutError:
            logger.debug("Preview still running for %s; falling back", url)
            return None
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - a preview is never a hard failure
            logger.debug("Preview failed for %s: %s", url, exc)
            return None
        return self.cached(url)

    def warm(self, url: str) -> None:
        """Start a probe now without waiting for it (cache warming)."""
        if self._timeout <= 0 or self.cached(url) is not None:
            return
        task = self._inflight.get(url)
        if task is None or task.done():
            self._inflight[url] = asyncio.create_task(self._fetch(url), name="preview-warm")

    def forget(self, url: Optional[str] = None) -> None:
        if url is None:
            self._cache.clear()
        else:
            self._cache.pop(url, None)

    # --- internals ----------------------------------------------------------
    async def _fetch(self, url: str) -> None:
        info: Optional[MediaInfo] = None
        timer = Timer()
        outcome = "empty"
        try:
            info = await asyncio.wait_for(self._probe.probe(url), timeout=self._timeout)
            outcome = "ok" if info is not None else "empty"
        except asyncio.CancelledError:
            raise
        except asyncio.TimeoutError:
            outcome = "timeout"
            logger.debug("Preview timed out after %ss for %s", self._timeout, url)
        except Exception as exc:  # noqa: BLE001 - best effort by design
            outcome = "error"
            logger.debug("Preview failed for %s: %s", url, exc)
        finally:
            self._inflight.pop(url, None)
            pipeline("preview", host=_host(url), ms=timer.elapsed, outcome=outcome)

        if info is None:
            return
        self._cache[url] = (info, time.monotonic())
        self._prune()

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
