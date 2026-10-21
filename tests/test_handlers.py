"""Handler-level behaviour: the join gate, queueing and instant cancellation."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from linktofile.application.active import ActiveJobs
from linktofile.application.queue import DownloadQueue
from linktofile.application.ratelimit import RateLimiter
from linktofile.application.settings import SettingsService
from linktofile.domain.policies import FormatPolicy
from linktofile.infrastructure.persistence.sqlite import (
    SqliteSettings,
    SqliteStats,
    SqliteTickets,
    SqliteUsers,
)
from linktofile.infrastructure.telegram import handlers, texts
from linktofile.infrastructure.telegram.callbacks import TicketCB

USER = SimpleNamespace(id=7, first_name="Ada", is_bot=False)


class FakeMessage:
    def __init__(self, text: str = "") -> None:
        self.text = text
        self.chat = SimpleNamespace(id=7)
        self.from_user = USER
        self.answers: list[str] = []
        self.edits: list[str] = []
        self.deleted = False

    async def answer(self, text, **kwargs):
        self.answers.append(text)
        return self

    async def edit_text(self, text, **kwargs):
        self.edits.append(text)
        return self

    async def delete(self):
        self.deleted = True


class FakeCallback:
    def __init__(self, message: FakeMessage) -> None:
        self.message = message
        self.from_user = USER
        self.alerts: list[str] = []

    async def answer(self, text: str = "", show_alert: bool = False):
        if text:
            self.alerts.append(text)


class FakeWorkflow:
    def __init__(self) -> None:
        self.calls: list[int] = []
        self.temp_dir = None
        self.closed = False

    async def run(self, job, delivery, *, on_progress=None, cancel_event=None):
        self.calls.append(job.ticket_id)

    async def close(self) -> None:
        self.closed = True


class FakeAccess:
    def __init__(self, allowed: bool = True) -> None:
        self.allowed = allowed

    async def allows(self, user_id: int) -> bool:
        return self.allowed

    def forget_membership_cache(self) -> None:
        return None


class FakeMembership:
    async def resolve_channel(self, channel):
        return None


@pytest.fixture
def services(database):
    settings_repo = SqliteSettings(database)
    settings = SettingsService(settings_repo)
    users = SqliteUsers(database)
    users.register(USER.id)
    queue = DownloadQueue(concurrency=1, per_user_limit=5, max_size=10)
    workflow = FakeWorkflow()
    workflow.temp_dir = __import__("pathlib").Path("/tmp")
    return SimpleNamespace(
        bot=SimpleNamespace(),
        settings=settings,
        access=FakeAccess(),
        membership=FakeMembership(),
        tickets=SqliteTickets(database),
        users=users,
        stats=SqliteStats(database),
        settings_repo=settings_repo,
        queue=queue,
        workflow=workflow,
        policy=FormatPolicy(cap_bytes=1024 * 1024),
        limiter=RateLimiter(0),
        active=ActiveJobs(),
        cap_mb=50,
        admin_id=1,
        version="3.0.0",
        is_admin=lambda user_id: user_id == 1,
    )


async def test_a_link_is_turned_into_a_ticket_and_queued(services):
    services.queue.start()
    message = FakeMessage("https://example.com/file.zip")

    await handlers.on_text(message, services)
    await services.queue.drain(timeout=5)

    assert services.tickets.recent(USER.id)[0].url == "https://example.com/file.zip"
    assert len(services.workflow.calls) == 1
    assert message.answers  # queued card
    await services.queue.stop()


async def test_media_links_ask_for_a_quality_first(services):
    services.queue.start()
    message = FakeMessage("https://youtu.be/dQw4w9WgXcQ")

    await handlers.on_text(message, services)

    assert "Pick a quality" in message.answers[0]
    assert services.workflow.calls == []
    await services.queue.stop()


async def test_the_join_gate_blocks_downloads(services):
    services.access = FakeAccess(allowed=False)
    services.settings.update_force_join(enabled=True, channel="@chan")
    message = FakeMessage("https://example.com/file.zip")

    await handlers.on_text(message, services)

    assert services.tickets.recent(USER.id) == []
    assert "Join" in message.answers[0]
    assert services.workflow.calls == []


async def test_a_blocked_range_is_refused_before_queueing(services):
    local = FakeMessage("http://127.0.0.1:8080/secret")
    await handlers.on_text(local, services)

    assert texts.blocked_url_text() in local.answers
    assert services.tickets.recent(USER.id) == []


async def test_cancel_button_stops_immediately_and_edits_the_card(services):
    services.queue.start()
    blocker = asyncio.Event()
    started = asyncio.Event()

    async def stuck(job, delivery, **kwargs):
        started.set()
        await blocker.wait()

    services.workflow.run = stuck
    message = FakeMessage("https://example.com/file.zip")

    await handlers.on_text(message, services)
    await asyncio.wait_for(started.wait(), 2)
    ticket = services.tickets.recent(USER.id)[0]

    callback = FakeCallback(message)
    await handlers.on_ticket(callback, TicketCB(action="cancel", ticket=ticket.id), services)

    assert callback.alerts == ["🛑 Cancelled"]
    assert texts.cancel_text() in message.edits
    blocker.set()
    await services.queue.drain(timeout=5)
    await services.queue.stop()


async def test_cancel_for_someone_elses_ticket_is_refused(services):
    services.users.register(999)
    ticket = services.tickets.create(999, "https://example.com/x")
    message = FakeMessage()
    callback = FakeCallback(message)

    await handlers.on_ticket(callback, TicketCB(action="cancel", ticket=ticket.id), services)

    assert callback.alerts == [texts.missing_download_text()]
    assert message.edits == []
