"""Background housekeeping: delete temp files no live job owns."""

from __future__ import annotations

import asyncio
import logging
from typing import Callable, Iterable

logger = logging.getLogger(__name__)


async def janitor_loop(
    sweep: Callable[[float, set[int]], int],
    *,
    interval: int,
    max_age: int,
    active: Callable[[], Iterable[int]],
) -> None:
    while True:
        try:
            removed = sweep(float(max_age), set(active()))
            if removed:
                logger.info("Janitor removed %s stale file(s)", removed)
        except Exception as exc:  # noqa: BLE001 - never kill the loop
            logger.warning("Janitor sweep failed: %s", exc)
        await asyncio.sleep(interval)
