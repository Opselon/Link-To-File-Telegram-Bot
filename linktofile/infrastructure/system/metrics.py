"""Cheap process/host metrics for the admin dashboard.

Everything here is best-effort: a missing ``/proc`` entry must never break the
panel, so each probe degrades to a neutral value.
"""

from __future__ import annotations

import os
import time
from pathlib import Path
from typing import Optional

_BOOT = time.monotonic()


def uptime_text(now: Optional[float] = None) -> str:
    seconds = max(0.0, (now if now is not None else time.monotonic()) - _BOOT)
    if seconds < 60:
        return f"{int(seconds)}s"
    if seconds < 3600:
        return f"{int(seconds // 60)}m"
    if seconds < 86400:
        return f"{int(seconds // 3600)}h {int((seconds % 3600) // 60)}m"
    return f"{int(seconds // 86400)}d {int((seconds % 86400) // 3600)}h"


def rss_mb() -> int:
    """Resident set size of this process, in MB (0 when unreadable)."""
    try:
        for line in Path("/proc/self/status").read_text().splitlines():
            if line.startswith("VmRSS:"):
                return int(line.split()[1]) // 1024
    except (OSError, ValueError, IndexError):
        pass
    return 0


def directory_bytes(path: str | Path) -> int:
    total = 0
    try:
        for entry in os.scandir(path):
            try:
                if entry.is_file(follow_symlinks=False):
                    total += entry.stat(follow_symlinks=False).st_size
            except OSError:
                continue
    except OSError:
        return 0
    return total


__all__ = ["directory_bytes", "rss_mb", "uptime_text"]
