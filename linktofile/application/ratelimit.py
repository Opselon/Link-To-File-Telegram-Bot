"""A tiny in-memory rate limiter (per user), kept out of the handlers."""

from __future__ import annotations

import time


class RateLimiter:
    def __init__(self, seconds: int) -> None:
        self._seconds = max(0, int(seconds))
        self._last: dict[int, float] = {}

    @property
    def enabled(self) -> bool:
        return self._seconds > 0

    def remaining(self, user_id: int) -> int:
        if not self.enabled:
            return 0
        last = self._last.get(user_id)
        if last is None:
            return 0
        left = self._seconds - (time.monotonic() - last)
        return max(0, int(left) + (1 if left > 0 else 0))

    def hit(self, user_id: int) -> bool:
        """Record a request. Returns False when the user must wait."""
        if not self.enabled:
            return True
        if self.remaining(user_id):
            return False
        self._last[user_id] = time.monotonic()
        return True
