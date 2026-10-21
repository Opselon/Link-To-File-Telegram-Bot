"""Structured one-line pipeline logs.

Every stage of a download writes a single greppable line::

    [PIPELINE:download.done] ticket=14 host=instagram.com bytes=3576505 ms=6140

The point is that a failure can be reconstructed from ``docker logs`` alone:
which lane the link took, what the extractor returned, how long each stage
took and why the job ended. Free-text messages stay in the stage loggers; these
lines are the machine-readable spine.
"""

from __future__ import annotations

import logging
import time
from typing import Any

logger = logging.getLogger("linktofile.pipeline")

_MAX_VALUE = 200


def _render(key: str, value: Any) -> str:
    text = str(value).replace("\n", " ").replace("\r", " ")
    if len(text) > _MAX_VALUE:
        text = text[: _MAX_VALUE - 3] + "..."
    if not text or any(char in text for char in ' ="'):
        return f'{key}="{text.replace(chr(34), chr(39))}"'
    return f"{key}={text}"


def pipeline(stage: str, *, ticket: Any = None, level: int = logging.INFO, **fields: Any) -> None:
    """Emit ``[PIPELINE:<stage>] ticket=… key=value …`` at ``level``."""
    parts = [f"[PIPELINE:{stage}]"]
    if ticket is not None:
        parts.append(f"ticket={ticket}")
    for key, value in fields.items():
        if value is None or value == "":
            continue
        parts.append(_render(key, value))
    logger.log(level, " ".join(parts))


class Timer:
    """Millisecond timer for stage logs (``ms=t.elapsed``)."""

    __slots__ = ("_started",)

    def __init__(self) -> None:
        self._started = time.monotonic()

    @property
    def elapsed_ms(self) -> int:
        return int((time.monotonic() - self._started) * 1000)

    @property
    def elapsed(self) -> int:
        return self.elapsed_ms

    def __float__(self) -> float:
        return self.elapsed_ms / 1000


__all__ = ["pipeline", "Timer", "logger"]
