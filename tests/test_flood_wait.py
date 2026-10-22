"""Flood-wait handling: Telegram's only self-diagnosing failure.

A ``TelegramRetryAfter`` carries the exact wait. Absorbing it inside the
delivery keeps queued work alive instead of failing a ticket the bot could
perfectly have sent five seconds later.
"""

from __future__ import annotations


import pytest
from aiogram.exceptions import TelegramRetryAfter

from linktofile.domain.errors import BotError
from linktofile.infrastructure.telegram.flood import send_with_flood_wait


def _retry_after(retry_after: int = 1) -> TelegramRetryAfter:
    method = type("M", (), {"__api_method__": "sendMessage"})()
    return TelegramRetryAfter(method=method, message="Too Many Requests", retry_after=retry_after)


async def test_a_short_wait_is_honoured_and_the_send_retries():
    calls = []
    waited = []

    async def action():
        calls.append(1)
        if len(calls) == 1:
            raise _retry_after(2)
        return "ok"

    async def record(delay):
        waited.append(delay)

    result = await send_with_flood_wait(action, ticket=11, stage="upload", sleep=record)

    assert result == "ok"
    assert len(calls) == 2
    assert waited == [2]  # the platform's own number, not a guess


async def test_a_second_retry_after_gives_up_cleanly():
    calls = []

    async def action():
        calls.append(1)
        raise _retry_after(1)

    with pytest.raises(BotError) as excinfo:
        await send_with_flood_wait(action, sleep=_never_sleeps)

    assert "slow down" in str(excinfo.value).lower()
    assert len(calls) == 2


async def test_a_demand_beyond_the_cap_is_refused_instead_of_blocking():
    async def action():
        raise _retry_after(600)

    with pytest.raises(BotError) as excinfo:
        await send_with_flood_wait(action, max_wait=60, sleep=_never_sleeps)

    assert "wait a while" in str(excinfo.value).lower()


async def test_success_never_sleeps():
    waited = []

    async def action():
        return "sent"

    async def record(delay):
        waited.append(delay)

    result = await send_with_flood_wait(action, sleep=record)

    assert result == "sent"
    assert waited == []


async def test_a_wait_exactly_at_the_cap_is_still_honoured():
    waited = []

    async def action():
        if not waited:
            raise _retry_after(5)
        return "sent"

    async def record(delay):
        waited.append(delay)

    result = await send_with_flood_wait(action, max_wait=5, sleep=record)

    assert result == "sent"
    assert waited == [5]


async def test_unrelated_errors_are_not_swallowed():
    async def action():
        raise ValueError("not a flood wait")

    # A non-Telegram failure must not be retried or hidden behind BotError.
    await _assert_not_wrapped(action)


async def _never_sleeps(delay) -> None:
    return None


async def _assert_not_wrapped(action) -> None:
    try:
        await send_with_flood_wait(action, sleep=_never_sleeps)
    except ValueError:
        return
    except BotError as exc:  # pragma: no cover - only on a regression
        pytest.fail(f"unrelated error was wrapped: {exc}")
    pytest.fail("unrelated error vanished")
