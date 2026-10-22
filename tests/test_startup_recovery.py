"""Boot wiring: the dispatcher startup hook is what actually resumes the queue.

If this seam is wrong, recovery is dead code — the rows exist, but nothing ever
replays them. So the test drives ``on_startup`` itself, with a recording bot.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from linktofile.application.active import ActiveJobs
from linktofile.application.container import Services
from linktofile.application.downloads import DownloadWorkflow
from linktofile.application.queue import DownloadQueue
from linktofile.application.recovery import QueueRecovery
from linktofile.application.settings import SettingsService
from linktofile.domain.models import DownloadStatus, MediaAsset, Quality
from linktofile.domain.policies import FormatPolicy
from linktofile.infrastructure.persistence.sqlite import (
    Database,
    SqliteSettings,
    SqliteStats,
    SqliteTickets,
    SqliteUsers,
)
from linktofile.infrastructure.telegram import app as telegram_app
from linktofile.infrastructure.telegram.resume import HeadlessDelivery
from linktofile.shared import security

USER = 7
URL = "https://example.com/queued.bin"


class FakeBot:
    def __init__(self) -> None:
        self.documents: list[dict] = []
        self.messages: list[str] = []
        self.profile_calls: list[str] = []

    async def send_document(self, chat_id, document, **kwargs):
        path = Path(getattr(document, "path", str(document)))
        self.documents.append({"chat_id": chat_id, "path": path, **kwargs})
        return SimpleNamespace(message_id=len(self.documents))

    async def send_video(self, chat_id, video, **kwargs):
        return await self.send_document(chat_id, video, **kwargs)

    async def send_audio(self, chat_id, audio, **kwargs):
        return await self.send_document(chat_id, audio, **kwargs)

    async def send_message(self, chat_id, text, **kwargs):
        self.messages.append(text)
        return SimpleNamespace(message_id=0)

    async def set_my_commands(self, commands):
        self.profile_calls.append("commands")

    async def set_my_name(self, name):
        self.profile_calls.append("name")

    async def set_my_description(self, description):
        self.profile_calls.append("description")


class StubEngine:
    def __init__(self, tmp: Path) -> None:
        self.tmp = tmp

    async def fetch(self, url, ticket_id, quality, *, on_progress=None, cancel_event=None):
        path = self.tmp / f"{ticket_id}_boot.bin"
        path.write_bytes(b"boot-payload")
        return MediaAsset(path=path, size=12, title="boot")

    async def close(self) -> None:
        return None


@pytest.fixture
def wired(tmp_path):
    database = Database(tmp_path / "bot.db")
    users = SqliteUsers(database)
    users.register(USER)
    tickets = SqliteTickets(database)
    bot = FakeBot()
    workflow = DownloadWorkflow(
        engine=StubEngine(tmp_path),
        tickets=tickets,
        policy=FormatPolicy(cap_bytes=1024 * 1024),
        temp_dir=tmp_path,
        min_free_mb=0,
        retries=1,
        backoff_seconds=0,
    )
    queue = DownloadQueue(concurrency=2, per_user_limit=5, max_size=20, task_timeout=10)
    active = ActiveJobs()
    services = Services(
        bot=bot,
        settings=SettingsService(SqliteSettings(database)),
        access=SimpleNamespace(),
        membership=SimpleNamespace(),
        tickets=tickets,
        users=users,
        stats=SqliteStats(database),
        settings_repo=SqliteSettings(database),
        queue=queue,
        workflow=workflow,
        policy=FormatPolicy(cap_bytes=1024 * 1024),
        limiter=SimpleNamespace(),
        active=active,
        preview=SimpleNamespace(),
        prefs=SimpleNamespace(),
        cap_mb=50,
        admin_id=1,
        version="3.3.0",
        recovery=QueueRecovery(
            tickets=tickets,
            queue=queue,
            workflow=workflow,
            active=active,
            delivery_factory=lambda job: HeadlessDelivery(
                bot, job.user_id, ticket_id=job.ticket_id
            ),
        ),
    )
    return SimpleNamespace(services=services, bot=bot, tickets=tickets, tmp=tmp_path)


@pytest.fixture(autouse=True)
def _no_ssrf():
    security.set_enforcement(False)
    yield
    security.set_enforcement(None)


async def test_startup_replays_the_queue_and_delivers(wired):
    ticket = wired.tickets.create(USER, URL)
    wired.tickets.enqueue(ticket.id, Quality.video(720))

    await telegram_app.on_startup(wired.services, wired.bot)
    try:
        await wired.services.queue.drain(timeout=10)
    finally:
        await telegram_app.on_shutdown(wired.services, wired.bot)

    assert wired.services.recovered == 1
    assert wired.tickets.get(ticket.id).status is DownloadStatus.COMPLETED
    assert len(wired.bot.documents) == 1
    assert wired.bot.documents[0]["chat_id"] == USER
    assert "commands" in wired.bot.profile_calls


async def test_startup_closes_jobs_the_previous_process_was_running(wired):
    ticket = wired.tickets.create(USER, URL)
    wired.tickets.enqueue(ticket.id, Quality.video(720))
    wired.tickets.update(ticket.id, DownloadStatus.DOWNLOADING)

    await telegram_app.on_startup(wired.services, wired.bot)
    try:
        await wired.services.queue.drain(timeout=10)
    finally:
        await telegram_app.on_shutdown(wired.services, wired.bot)

    assert wired.tickets.get(ticket.id).status is DownloadStatus.FAILED
    assert wired.bot.documents == []


async def test_startup_without_recovery_falls_back_to_the_reaper(wired):
    wired.services.recovery = None
    ticket = wired.tickets.create(USER, URL)
    wired.tickets.update(ticket.id, DownloadStatus.DOWNLOADING)

    await telegram_app.on_startup(wired.services, wired.bot)
    try:
        await wired.services.queue.drain(timeout=5)
    finally:
        await telegram_app.on_shutdown(wired.services, wired.bot)

    assert wired.tickets.get(ticket.id).status is DownloadStatus.FAILED


async def test_headless_delivery_sends_the_file_with_a_caption(wired):
    delivery = HeadlessDelivery(wired.bot, USER, ticket_id=99, admin_id=1)

    await delivery.delivered(_asset(wired.tmp, 99))

    assert len(wired.bot.documents) == 1
    assert wired.bot.documents[0]["caption"]
    assert wired.bot.documents[0]["size"] if "size" in wired.bot.documents[0] else True


def _asset(tmp: Path, ticket_id: int) -> MediaAsset:
    path = tmp / f"{ticket_id}_movie.mp4"
    path.write_bytes(b"x" * 32)
    return MediaAsset(path=path, size=32, title="movie", duration=5, width=1280, height=720)


async def test_headless_delivery_failed_needs_a_stringable_error(wired):
    from linktofile.domain.errors import BotError

    delivery = HeadlessDelivery(wired.bot, USER, ticket_id=100)
    await delivery.failed(BotError("network said no"))
    assert "network said no" in wired.bot.messages[-1]


async def test_headless_delivery_cancelled_is_a_message(wired):
    delivery = HeadlessDelivery(wired.bot, USER, ticket_id=101)
    await delivery.cancelled()
    assert wired.bot.messages
