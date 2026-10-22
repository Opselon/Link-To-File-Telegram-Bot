"""Hardening at the handler layer: dedupe, durable cancels, admin sub-views."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from linktofile.application.active import ActiveJobs
from linktofile.application.preferences import UserPreferences
from linktofile.application.queue import DownloadQueue
from linktofile.application.ratelimit import RateLimiter
from linktofile.application.settings import SettingsService
from linktofile.domain.models import DownloadStatus
from linktofile.domain.policies import FormatPolicy
from linktofile.infrastructure.persistence.sqlite import (
    SqliteSettings,
    SqliteStats,
    SqliteTickets,
    SqliteUsers,
)
from linktofile.infrastructure.telegram import handlers
from linktofile.infrastructure.telegram.callbacks import AdminCB, QualityCB, TicketCB

from test_handlers import FakeCallback, FakeMessage, StubPreview, USER


class FakeWorkflow:
    def __init__(self, tmp: Path) -> None:
        self.calls: list[int] = []
        self.temp_dir = tmp
        self.closed = False

    async def run(self, job, delivery, *, on_progress=None, cancel_event=None):
        self.calls.append(job.ticket_id)

    async def close(self) -> None:
        self.closed = True


class FakeAccess:
    def __init__(self, allowed: bool) -> None:
        self.allowed = allowed

    async def allows(self, user_id: int) -> bool:
        return self.allowed

    def forget_membership_cache(self) -> None:
        return None


@pytest.fixture
def services(database, tmp_path):
    settings_repo = SqliteSettings(database)
    users = SqliteUsers(database)
    users.register(USER.id)
    return SimpleNamespace(
        bot=SimpleNamespace(),
        settings=SettingsService(settings_repo),
        access=FakeAccess(allowed=True),
        membership=SimpleNamespace(),
        tickets=SqliteTickets(database),
        users=users,
        stats=SqliteStats(database),
        settings_repo=settings_repo,
        queue=DownloadQueue(concurrency=4, per_user_limit=5, max_size=10),
        workflow=FakeWorkflow(tmp_path),
        policy=FormatPolicy(cap_bytes=1024 * 1024),
        limiter=RateLimiter(0),
        active=ActiveJobs(),
        prefs=UserPreferences(settings_repo),
        preview=StubPreview(),
        cap_mb=50,
        admin_id=1,
        version="3.3.0",
        recovered=1,
        is_admin=lambda user_id: user_id == 1,
    )


async def test_the_same_link_cannot_run_twice_for_a_user(services):
    services.queue.start()
    first = FakeMessage("https://example.com/file.zip")
    await handlers.on_text(first, services)

    second = FakeMessage("https://example.com/file.zip")
    await handlers.on_text(second, services)
    await services.queue.drain(timeout=10)
    await services.queue.stop()

    assert "already queued" in second.answers[0].lower()
    assert len(services.workflow.calls) == 1
    # The duplicate row records *why* it did not run, for the error panel.
    duplicate = next(
        t for t in services.tickets.recent(USER.id, limit=10) if t.status is DownloadStatus.FAILED
    )
    assert duplicate.error_kind == "duplicate"


async def test_the_cancel_button_persists_the_cancelled_status(services):
    services.queue.start()
    blocker = FakeMessage("https://example.com/late.zip")
    await handlers.on_text(blocker, services)
    ticket = services.tickets.recent(USER.id)[0]

    card = FakeMessage()
    callback = FakeCallback(card)
    await handlers.on_ticket(callback, TicketCB(action="cancel", ticket=ticket.id), services)
    await services.queue.drain(timeout=5)
    await services.queue.stop()

    assert services.tickets.get(ticket.id).status is DownloadStatus.CANCELLED
    # No zombie: nothing is left queued for the restart to resurrect.
    assert services.tickets.queue_contents() == []


async def test_admin_can_open_the_queue_and_error_views(services):
    editor = FakeMessage()
    callback = FakeCallback(editor)
    callback.from_user = SimpleNamespace(id=1, first_name="Admin")
    state = SimpleNamespace(clear=lambda: None)

    await handlers.on_admin(callback, AdminCB(action="queue"), services, state)
    assert "Nothing is queued" in editor.edits[0]

    await handlers.on_admin(callback, AdminCB(action="errors"), services, state)
    assert "No failed tickets" in editor.edits[-1]


async def test_the_admin_dashboard_carries_recovered_and_temp_stats(services):
    view = handlers._admin_view(services)
    assert "Resumed this boot: <b>1</b>" in view
    assert "temp:" in view


async def test_a_quality_choice_enqueues_and_sets_the_preference(services):
    services.queue.start()
    ticket = services.tickets.create(USER.id, "https://youtu.be/dQw4w9WgXcQ")
    editor = FakeMessage()
    callback = FakeCallback(editor)
    callback.message.photo = None
    await handlers.on_quality(
        callback, QualityCB(kind="video", value="720", ticket=ticket.id), services
    )
    await services.queue.drain(timeout=10)
    await services.queue.stop()

    assert services.workflow.calls == [ticket.id]
    assert services.prefs.get(USER.id).label == "720p"
