"""Handler-level behaviour: the join gate, queueing and instant cancellation."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from linktofile.application.active import ActiveJobs
from linktofile.application.preferences import UserPreferences
from linktofile.application.queue import DownloadQueue
from linktofile.application.ratelimit import RateLimiter
from linktofile.application.settings import SettingsService
from linktofile.domain.models import MediaInfo, Quality
from linktofile.domain.policies import FormatPolicy
from linktofile.infrastructure.persistence.sqlite import (
    SqliteSettings,
    SqliteStats,
    SqliteTickets,
    SqliteUsers,
)
from linktofile.infrastructure.telegram import handlers, texts
from linktofile.infrastructure.telegram.callbacks import QualityCB, TicketCB

USER = SimpleNamespace(id=7, first_name="Ada", is_bot=False)


class FakeMessage:
    def __init__(self, text: str = "") -> None:
        self.text = text
        self.chat = SimpleNamespace(id=7)
        self.from_user = USER
        self.answers: list[str] = []
        self.edits: list[str] = []
        self.photos: list[str] = []
        self.markups: list = []
        self.deleted = False

    async def answer(self, text, **kwargs):
        self.answers.append(text)
        self.markups.append(kwargs.get("reply_markup"))
        return self

    async def answer_photo(self, photo, **kwargs):
        self.photos.append(str(photo))
        self.answers.append(kwargs.get("caption") or "")
        self.markups.append(kwargs.get("reply_markup"))
        return self

    async def edit_text(self, text, **kwargs):
        self.edits.append(text)
        self.markups.append(kwargs.get("reply_markup"))
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


class StubPreview:
    """No network in tests: the preview service returns whatever we hand it."""

    def __init__(self, info=None) -> None:
        self.info = info
        self.calls: list[str] = []

    async def describe(self, url: str):
        self.calls.append(url)
        return self.info


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
        prefs=UserPreferences(settings_repo),
        preview=StubPreview(),
        cap_mb=50,
        admin_id=1,
        version="3.1.0",
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


# --- v3.1: smart preview + remembered quality -------------------------------
INFO = MediaInfo(
    title="Ada talk",
    uploader="Ada",
    duration=95,
    thumbnail="https://cdn.example/t.jpg",
    site="youtube.com",
)


async def test_a_media_link_shows_a_rich_preview_card(services):
    services.preview = StubPreview(INFO)
    message = FakeMessage("https://www.youtube.com/watch?v=dQw4w9WgXcQ")

    await handlers.on_text(message, services)

    assert message.photos == ["https://cdn.example/t.jpg"]
    assert "Ada talk" in message.answers[0]
    assert "Pick a quality" in message.answers[0]
    assert services.workflow.calls == []  # nothing downloads before a choice
    assert services.tickets.recent(USER.id)[0].url.endswith("dQw4w9WgXcQ")


async def test_a_failed_preview_falls_back_to_the_plain_picker(services):
    services.preview = StubPreview(None)
    message = FakeMessage("https://youtu.be/dQw4w9WgXcQ")

    await handlers.on_text(message, services)

    assert message.photos == []
    assert "Pick a quality" in message.answers[0]
    assert message.markups[-1] is not None


async def test_direct_file_links_are_never_probed(services):
    services.queue.start()
    message = FakeMessage("https://cdn.example/song.mp3")

    await handlers.on_text(message, services)

    assert services.preview.calls == []
    await services.queue.drain(timeout=5)
    await services.queue.stop()


async def test_the_remembered_quality_tops_the_picker(services):
    services.prefs.set(USER.id, Quality.video(720))
    message = FakeMessage("https://youtu.be/dQw4w9WgXcQ")

    await handlers.on_text(message, services)

    first_row = message.markups[-1].inline_keyboard[0]
    payloads = [QualityCB.unpack(button.callback_data) for button in first_row]
    assert payloads[0].kind == "video" and payloads[0].value == "720"
    assert payloads[1].kind == "forget"


async def test_forget_clears_the_remembered_quality(services):
    services.prefs.set(USER.id, Quality.audio(192))
    ticket = services.tickets.create(USER.id, "https://youtu.be/dQw4w9WgXcQ")
    message = FakeMessage()
    callback = FakeCallback(message)

    await handlers.on_quality(
        callback, QualityCB(kind="forget", value="-", ticket=ticket.id), services
    )

    assert services.prefs.get(USER.id) is None
    assert callback.alerts == ["Preference cleared ↺"]


async def test_picking_a_quality_is_remembered(services):
    services.queue.start()
    ticket = services.tickets.create(USER.id, "https://youtu.be/dQw4w9WgXcQ")
    callback = FakeCallback(FakeMessage())

    await handlers.on_quality(
        callback, QualityCB(kind="video", value="480", ticket=ticket.id), services
    )

    assert services.prefs.get(USER.id) == Quality.video(480)
    await services.queue.drain(timeout=5)
    await services.queue.stop()


def test_plain_text_is_closed_while_an_admin_flow_is_open():
    """A link must not be swallowed by waiting_channel / waiting_broadcast."""
    from aiogram.filters import StateFilter

    handler = next(
        item
        for item in handlers.router.message.handlers
        if getattr(item.callback, "__name__", "") == "on_text"
    )
    states = [f.callback for f in handler.filters if isinstance(f.callback, StateFilter)]

    assert states, "on_text must carry a StateFilter"
    assert states[0].states == (None,)
