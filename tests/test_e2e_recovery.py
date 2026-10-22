"""End-to-end: link → durable queue → real download → Telegram, through a restart.

No external service: the file is served by an in-process aiohttp server, and
Telegram is a recording fake. The point is the seam between generations of the
process — generation 2 must pick up exactly what generation 1 committed.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest
from aiohttp import web

from linktofile.application.active import ActiveJobs
from linktofile.application.downloads import DownloadWorkflow
from linktofile.application.queue import DownloadQueue
from linktofile.application.recovery import QueueRecovery
from linktofile.domain.models import DownloadStatus, Quality
from linktofile.domain.policies import FormatPolicy
from linktofile.infrastructure.download.adapter import EngineDownloadAdapter
from linktofile.infrastructure.persistence.sqlite import (
    Database,
    SqliteTickets,
    SqliteUsers,
)
from linktofile.infrastructure.telegram.resume import HeadlessDelivery
from linktofile.shared import security

USER = 7
PAYLOAD = b"e2e-payload" * 256


class FakeBot:
    """Records what a real bot would have sent."""

    def __init__(self) -> None:
        self.documents: list[dict] = []
        self.messages: list[str] = []

    async def send_document(self, chat_id, document, **kwargs):
        path = Path(getattr(document, "path", str(document)))
        self.documents.append(
            {"chat_id": chat_id, "path": path, "size": path.stat().st_size, **kwargs}
        )
        return SimpleNamespace(message_id=len(self.documents))

    async def send_video(self, chat_id, video, **kwargs):
        return await self.send_document(chat_id, video, **kwargs)

    async def send_audio(self, chat_id, audio, **kwargs):
        return await self.send_document(chat_id, audio, **kwargs)

    async def send_message(self, chat_id, text, **kwargs):
        self.messages.append(text)
        return SimpleNamespace(message_id=0)


@pytest.fixture(autouse=True)
def _local_server_allowed():
    security.set_enforcement(False)
    yield
    security.set_enforcement(None)


@pytest.fixture
async def file_server(aiohttp_server):
    async def clip(request):
        return web.Response(
            body=PAYLOAD,
            headers={
                "Content-Type": "video/mp4",
                "Content-Disposition": 'attachment; filename="clip.mp4"',
            },
        )

    app = web.Application()
    app.router.add_get("/clip.mp4", clip)
    return await aiohttp_server(app)


def _generation(db_path: Path, bot: FakeBot, temp_dir: Path):
    """One 'process': its own database handle, queue, workflow and recovery."""
    database = Database(db_path)
    SqliteUsers(database).register(USER)
    tickets = SqliteTickets(database)
    workflow = DownloadWorkflow(
        engine=EngineDownloadAdapter(FormatPolicy(cap_bytes=1024 * 1024)),
        tickets=tickets,
        policy=FormatPolicy(cap_bytes=1024 * 1024),
        temp_dir=temp_dir,
        min_free_mb=0,
        retries=1,
        backoff_seconds=0,
    )
    queue = DownloadQueue(concurrency=2, per_user_limit=5, max_size=20, task_timeout=20)
    recovery = QueueRecovery(
        tickets=tickets,
        queue=queue,
        workflow=workflow,
        active=ActiveJobs(),
        delivery_factory=lambda job: HeadlessDelivery(
            bot, job.user_id, ticket_id=job.ticket_id
        ),
    )
    return SimpleNamespace(
        database=database,
        tickets=tickets,
        queue=queue,
        workflow=workflow,
        recovery=recovery,
        bot=bot,
    )


async def test_a_queued_download_survives_a_restart_and_is_delivered(file_server, tmp_path):
    from linktofile.config import DOWNLOADS_DIR

    url = str(file_server.make_url("/clip.mp4"))
    db_path = tmp_path / "bot.db"
    bot = FakeBot()

    # --- generation 1: accept the link, then die before running it ----------
    first = _generation(db_path, bot, DOWNLOADS_DIR)
    ticket = first.tickets.create(USER, url)
    first.tickets.enqueue(ticket.id, Quality.video(720))
    assert first.tickets.get(ticket.id).status is DownloadStatus.QUEUED

    # --- generation 2: boot, replay, download, deliver ----------------------
    second = _generation(db_path, bot, DOWNLOADS_DIR)
    second.queue.start()
    try:
        report = await second.recovery.resume()
        await second.queue.drain(timeout=30)
    finally:
        await second.queue.stop()

    assert report.resumed == 1
    stored = second.tickets.get(ticket.id)
    assert stored.status is DownloadStatus.COMPLETED
    assert stored.size == len(PAYLOAD)
    assert stored.filename and stored.filename.endswith("clip.mp4")

    [sent] = bot.documents
    assert sent["chat_id"] == USER
    assert sent["size"] == len(PAYLOAD)
    assert "<b>clip</b>" in sent["caption"]
    assert not sent["path"].exists()  # cleanup after delivery
    assert second.queue.pending == 0


async def test_a_download_running_during_a_crash_is_closed_not_resumed(file_server, tmp_path):
    from linktofile.config import DOWNLOADS_DIR

    url = str(file_server.make_url("/clip.mp4"))
    db_path = tmp_path / "bot.db"
    bot = FakeBot()

    first = _generation(db_path, bot, DOWNLOADS_DIR)
    ticket = first.tickets.create(USER, url)
    first.tickets.enqueue(ticket.id, Quality.video(720))
    first.tickets.update(ticket.id, DownloadStatus.DOWNLOADING)

    second = _generation(db_path, bot, DOWNLOADS_DIR)
    second.queue.start()
    try:
        await second.recovery.resume()
        await second.queue.drain(timeout=10)
    finally:
        await second.queue.stop()

    stored = second.tickets.get(ticket.id)
    assert stored.status is DownloadStatus.FAILED
    assert stored.error_kind == "interrupted"
    assert bot.documents == []


async def test_two_queued_links_both_finish_after_one_restart(file_server, tmp_path):
    from linktofile.config import DOWNLOADS_DIR

    url = str(file_server.make_url("/clip.mp4"))
    db_path = tmp_path / "bot.db"
    bot = FakeBot()

    first = _generation(db_path, bot, DOWNLOADS_DIR)
    ids = []
    for suffix in ("/clip.mp4", "/clip.mp4?second=1"):
        ticket = first.tickets.create(USER, url + suffix.replace("/clip.mp4", ""))
        first.tickets.enqueue(ticket.id, Quality.video(720))
        ids.append(ticket.id)

    second = _generation(db_path, bot, DOWNLOADS_DIR)
    second.queue.start()
    try:
        await second.recovery.resume()
        await second.queue.drain(timeout=30)
    finally:
        await second.queue.stop()

    assert all(second.tickets.get(i).status is DownloadStatus.COMPLETED for i in ids)
    assert len(bot.documents) == 2
