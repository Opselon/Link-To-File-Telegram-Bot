"""In-memory registry of live tickets (cancel events + per-user lookup)."""

from __future__ import annotations

import asyncio
from collections import defaultdict
from typing import Iterable, Optional


class ActiveJobs:
    def __init__(self) -> None:
        self._events: dict[int, asyncio.Event] = {}
        self._by_user: dict[int, set[int]] = defaultdict(set)

    def open(self, user_id: int, ticket_id: int) -> asyncio.Event:
        event = asyncio.Event()
        self._events[ticket_id] = event
        self._by_user[user_id].add(ticket_id)
        return event

    def event(self, ticket_id: int) -> Optional[asyncio.Event]:
        return self._events.get(ticket_id)

    def of_user(self, user_id: int) -> set[int]:
        return set(self._by_user.get(user_id, set()))

    def all(self) -> Iterable[int]:
        return list(self._events)

    def close(self, ticket_id: int) -> None:
        self._events.pop(ticket_id, None)
        for user_id, tickets in list(self._by_user.items()):
            tickets.discard(ticket_id)
            if not tickets:
                self._by_user.pop(user_id, None)
