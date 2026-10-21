"""The download use case, exercised with fakes: no network, no Telegram."""

import asyncio
from pathlib import Path

import pytest

from linktofile.application.downloads import DownloadJob, DownloadWorkflow
from linktofile.domain.errors import BotError, DownloadCancelled, MediaTooLarge
from linktofile.domain.models import DownloadStatus, MediaAsset, Quality
from linktofile.domain.policies import FormatPolicy
from linktofile.infrastructure.persistence.sqlite import SqliteTickets, SqliteUsers

CAP = 1024 * 1024


class FakeEngine:
    def __init__(self, *, size: int = 1024, error: Exception | None = None) -> None:
        self.size = size
        self.error = error
        self.seen_quality: Quality | None = None
        self.closed = False

    async def fetch(self, url, request_id, quality, *, on_progress=None, cancel_event=None):
        self.seen_quality = quality
        if self.error:
            raise self.error
        path = Path(__file__).parent / f"_fake_{request_id}.mp4"
        path.write_bytes(b"x" * self.size)
        if on_progress:
            on_progress(self.size, self.size, 1.0, 0.0)
        return MediaAsset(path=path, size=self.size, title="clip", duration=3, height=720)

    async def close(self):
        self.closed = True


class FakeDelivery:
    def __init__(self) -> None:
        self.events: list[str] = []
        self.errors: list[BotError] = []

    async def delivering(self, asset):
        self.events.append("delivering")

    async def delivered(self, asset):
        self.events.append("delivered")

    async def failed(self, error):
        self.events.append("failed")
        self.errors.append(error)

    async def cancelled(self):
        self.events.append("cancelled")


@pytest.fixture
def tickets(database):
    SqliteUsers(database).register(1)
    return SqliteTickets(database)


def workflow_for(tmp_path, tickets, engine):
    temp = tmp_path / "downloads"
    temp.mkdir(exist_ok=True)
    return DownloadWorkflow(
        engine=engine,
        tickets=tickets,
        policy=FormatPolicy(cap_bytes=CAP),
        temp_dir=temp,
        min_free_mb=0,
    )


def leftovers(tmp_path) -> list[Path]:
    return list((tmp_path / "downloads").glob("*"))


async def test_successful_job_delivers_then_deletes_the_file(tmp_path, tickets):
    engine = FakeEngine(size=2048)
    delivery = FakeDelivery()
    workflow = workflow_for(tmp_path, tickets, engine)
    ticket = tickets.create(1, "https://example.com/clip.mp4")

    await workflow.run(
        DownloadJob(ticket.id, 1, ticket.url, Quality.video(720)), delivery
    )

    assert delivery.events == ["delivering", "delivered"]
    assert tickets.get(ticket.id).status is DownloadStatus.COMPLETED
    assert leftovers(tmp_path) == []  # nothing left on disk
    assert engine.seen_quality == Quality.video(720)


async def test_oversized_asset_is_refused_and_cleaned(tmp_path, tickets):
    engine = FakeEngine(size=CAP + 1)
    delivery = FakeDelivery()
    workflow = workflow_for(tmp_path, tickets, engine)
    ticket = tickets.create(1, "https://example.com/huge.mp4")

    await workflow.run(DownloadJob(ticket.id, 1, ticket.url, Quality.video(None)), delivery)

    assert delivery.events == ["failed"]
    assert isinstance(delivery.errors[0], MediaTooLarge)
    assert tickets.get(ticket.id).status is DownloadStatus.FAILED
    assert leftovers(tmp_path) == []


async def test_cancelled_download_is_reported_and_cleaned(tmp_path, tickets):
    engine = FakeEngine(error=DownloadCancelled("stopped"))
    delivery = FakeDelivery()
    workflow = workflow_for(tmp_path, tickets, engine)
    ticket = tickets.create(1, "https://example.com/clip.mp4")

    await workflow.run(DownloadJob(ticket.id, 1, ticket.url, Quality.video(None)), delivery)

    assert delivery.events == ["cancelled"]
    assert tickets.get(ticket.id).status is DownloadStatus.CANCELLED


async def test_unexpected_error_never_leaks_a_file(tmp_path, tickets):
    engine = FakeEngine(size=10)
    delivery = FakeDelivery()
    workflow = workflow_for(tmp_path, tickets, engine)
    ticket = tickets.create(1, "https://example.com/clip.mp4")

    async def explode(asset):
        raise RuntimeError("upload failed")

    delivery.delivered = explode  # type: ignore[assignment]
    await workflow.run(DownloadJob(ticket.id, 1, ticket.url, Quality.video(None)), delivery)

    assert delivery.events == ["delivering", "failed"]
    assert tickets.get(ticket.id).status is DownloadStatus.FAILED
    assert leftovers(tmp_path) == []


async def test_low_disk_refuses_the_job_before_downloading(tmp_path, tickets):
    engine = FakeEngine()
    delivery = FakeDelivery()
    workflow = DownloadWorkflow(
        engine=engine,
        tickets=tickets,
        policy=FormatPolicy(cap_bytes=CAP),
        temp_dir=tmp_path,
        min_free_mb=10**9,
    )
    ticket = tickets.create(1, "https://example.com/clip.mp4")

    await workflow.run(DownloadJob(ticket.id, 1, ticket.url, Quality.video(None)), delivery)

    assert delivery.events == ["failed"]
    assert tickets.get(ticket.id).status is DownloadStatus.FAILED
    assert engine.seen_quality is None  # never touched the network


async def test_task_cancellation_marks_the_ticket_cancelled(tmp_path, tickets):
    started = asyncio.Event()

    class HangingEngine:
        async def fetch(self, *args, **kwargs):
            started.set()
            await asyncio.sleep(30)

        async def close(self):
            return None

    delivery = FakeDelivery()
    workflow = workflow_for(tmp_path, tickets, HangingEngine())
    ticket = tickets.create(1, "https://example.com/slow.mp4")
    task = asyncio.create_task(
        workflow.run(DownloadJob(ticket.id, 1, ticket.url, Quality.video(None)), delivery)
    )
    await asyncio.wait_for(started.wait(), 2)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    await asyncio.sleep(0)

    assert tickets.get(ticket.id).status is DownloadStatus.CANCELLED
    assert "cancelled" in delivery.events


async def test_sweep_removes_only_stale_orphans(tmp_path, tickets):
    workflow = workflow_for(tmp_path, tickets, FakeEngine())
    old = tmp_path / "downloads" / "999_old.mp4"
    old.write_bytes(b"x")
    old.touch()
    keep = tmp_path / "downloads" / "1_live.mp4"
    keep.write_bytes(b"x")

    removed = workflow.sweep(0, keep={1})
    assert removed == 1
    assert not old.exists()
    assert keep.exists()
