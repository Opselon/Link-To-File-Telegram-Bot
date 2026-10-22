"""Retry / backoff / cleanup behaviour of the download workflow.

The workflow owns the retry policy: the engine only says *what* went wrong
(``TransientDownloadFailed`` vs a terminal error), and the workflow decides
whether another attempt is worth it.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from linktofile.application.downloads import DownloadJob, DownloadWorkflow
from linktofile.domain.errors import (
    MediaUnavailable,
    TransientDownloadFailed,
)
from linktofile.domain.models import DownloadStatus, FailureKind, MediaAsset, Quality
from linktofile.domain.policies import FormatPolicy
from linktofile.infrastructure.persistence.sqlite import SqliteTickets, SqliteUsers

USER = 7
URL = "https://example.com/clip.mp4"


class StubEngine:
    """Replays a scripted list of outcomes (an exception or an asset path)."""

    def __init__(self, outcomes) -> None:
        self.outcomes = list(outcomes)
        self.calls = 0

    async def fetch(self, url, ticket_id, quality, *, on_progress=None, cancel_event=None):
        self.calls += 1
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome


class RecordingDelivery:
    def __init__(self) -> None:
        self.events: list[str] = []
        self.errors: list[str] = []
        self.sent: list[MediaAsset] = []

    async def delivering(self, asset):
        self.events.append("delivering")

    async def delivered(self, asset):
        self.events.append("delivered")
        self.sent.append(asset)

    async def failed(self, error):
        self.events.append("failed")
        self.errors.append(str(error))

    async def cancelled(self):
        self.events.append("cancelled")


@pytest.fixture
def repo(database):
    users = SqliteUsers(database)
    users.register(USER)
    return SqliteTickets(database)


def _asset(tmp: Path, ticket_id: int, size: int = 64) -> MediaAsset:
    path = tmp / f"{ticket_id}_clip.mp4"
    path.write_bytes(b"x" * size)
    return MediaAsset(path=path, size=size, title="clip")


def _workflow(repo, tmp_path, engine, **kwargs):
    return DownloadWorkflow(
        engine=engine,
        tickets=repo,
        policy=kwargs.pop("policy", FormatPolicy(cap_bytes=1024 * 1024)),
        temp_dir=tmp_path,
        min_free_mb=0,
        sleep=kwargs.pop("sleep", _no_sleep),
        **kwargs,
    )


async def _no_sleep(delay):
    return None


async def test_a_transient_failure_is_retried_and_then_succeeds(repo, tmp_path):
    ticket = repo.create(USER, URL)
    engine = StubEngine([TransientDownloadFailed("connection reset"), _asset(tmp_path, ticket.id)])
    delays: list[float] = []

    async def record(delay):
        delays.append(delay)

    workflow = _workflow(repo, tmp_path, engine, retries=3, backoff_seconds=1.0, sleep=record)
    delivery = RecordingDelivery()

    await workflow.run(DownloadJob(ticket.id, USER, URL, Quality.video(720)), delivery)

    assert engine.calls == 2
    assert delays == [1.0]
    assert repo.get(ticket.id).status is DownloadStatus.COMPLETED
    assert repo.get(ticket.id).attempts == 2
    assert delivery.events == ["delivering", "delivered"]


async def test_backoff_doubles_and_is_capped(repo, tmp_path):
    ticket = repo.create(USER, URL)
    engine = StubEngine(
        [TransientDownloadFailed("boom") for _ in range(3)] + [_asset(tmp_path, ticket.id)]
    )
    delays: list[float] = []

    async def record(delay):
        delays.append(delay)

    workflow = _workflow(
        repo,
        tmp_path,
        engine,
        retries=4,
        backoff_seconds=2.0,
        backoff_max=3.0,
        sleep=record,
    )

    await workflow.run(DownloadJob(ticket.id, USER, URL, Quality.video(720)), RecordingDelivery())

    assert delays == [2.0, 3.0, 3.0]
    assert repo.get(ticket.id).status is DownloadStatus.COMPLETED


async def test_exhausted_retries_fail_the_ticket_with_its_kind(repo, tmp_path):
    ticket = repo.create(USER, URL)
    engine = StubEngine([TransientDownloadFailed("reset")] * 3)

    workflow = _workflow(repo, tmp_path, engine, retries=3, backoff_seconds=0)
    delivery = RecordingDelivery()

    await workflow.run(DownloadJob(ticket.id, USER, URL, Quality.video(720)), delivery)

    stored = repo.get(ticket.id)
    assert engine.calls == 3
    assert stored.status is DownloadStatus.FAILED
    assert stored.error_kind == FailureKind.NETWORK.value
    assert "3 attempts" in (stored.error or "")
    assert delivery.events == ["failed"]


async def test_a_terminal_error_is_not_retried(repo, tmp_path):
    ticket = repo.create(USER, URL)
    engine = StubEngine([MediaUnavailable("this media is unavailable")])

    workflow = _workflow(repo, tmp_path, engine, retries=5, backoff_seconds=0)
    delivery = RecordingDelivery()

    await workflow.run(DownloadJob(ticket.id, USER, URL, Quality.video(720)), delivery)

    stored = repo.get(ticket.id)
    assert engine.calls == 1
    assert stored.status is DownloadStatus.FAILED
    assert stored.error_kind == FailureKind.UNAVAILABLE.value
    assert delivery.events == ["failed"]


async def test_a_cancel_during_backoff_stops_the_job(repo, tmp_path):
    ticket = repo.create(USER, URL)
    engine = StubEngine([TransientDownloadFailed("reset")])
    cancel = asyncio.Event()

    async def cancel_while_sleeping(delay):
        cancel.set()

    workflow = _workflow(
        repo, tmp_path, engine, retries=3, backoff_seconds=5.0, sleep=cancel_while_sleeping
    )
    delivery = RecordingDelivery()

    await workflow.run(
        DownloadJob(ticket.id, USER, URL, Quality.video(720)), delivery, cancel_event=cancel
    )

    assert engine.calls == 1
    assert repo.get(ticket.id).status is DownloadStatus.CANCELLED
    assert delivery.events == ["cancelled"]


async def test_a_cancel_before_the_first_attempt_never_downloads(repo, tmp_path):
    ticket = repo.create(USER, URL)
    engine = StubEngine([])
    cancel = asyncio.Event()
    cancel.set()

    workflow = _workflow(repo, tmp_path, engine)
    delivery = RecordingDelivery()

    await workflow.run(
        DownloadJob(ticket.id, USER, URL, Quality.video(720)), delivery, cancel_event=cancel
    )

    assert engine.calls == 0
    assert repo.get(ticket.id).status is DownloadStatus.CANCELLED


async def test_an_oversized_asset_is_refused_and_cleaned_up(repo, tmp_path):
    ticket = repo.create(USER, URL)
    asset = _asset(tmp_path, ticket.id, size=2048)
    engine = StubEngine([asset])

    workflow = _workflow(repo, tmp_path, engine, policy=FormatPolicy(cap_bytes=1024))
    delivery = RecordingDelivery()

    await workflow.run(DownloadJob(ticket.id, USER, URL, Quality.video(None)), delivery)

    stored = repo.get(ticket.id)
    assert stored.status is DownloadStatus.FAILED
    assert stored.error_kind == FailureKind.TOO_LARGE.value
    assert not Path(asset.path).exists()
    assert delivery.events == ["failed"]


async def test_the_temp_budget_refuses_new_work(repo, tmp_path):
    (tmp_path / "999_old.bin").write_bytes(b"z" * 5000)
    ticket = repo.create(USER, URL)
    engine = StubEngine([_asset(tmp_path, ticket.id)])

    workflow = _workflow(repo, tmp_path, engine, max_temp_bytes=1000)
    delivery = RecordingDelivery()

    await workflow.run(DownloadJob(ticket.id, USER, URL, Quality.video(720)), delivery)

    assert engine.calls == 0
    stored = repo.get(ticket.id)
    assert stored.status is DownloadStatus.FAILED
    assert "busy" in (stored.error or "").lower()


async def test_leftover_parts_are_removed_after_a_failure(repo, tmp_path):
    ticket = repo.create(USER, URL)
    (tmp_path / f"{ticket.id}_clip.mp4.part").write_bytes(b"half")
    engine = StubEngine([MediaUnavailable("gone")])

    workflow = _workflow(repo, tmp_path, engine)
    await workflow.run(DownloadJob(ticket.id, USER, URL, Quality.video(720)), RecordingDelivery())

    assert list(tmp_path.glob(f"{ticket.id}_*")) == []


async def test_a_resumed_job_continues_its_attempt_count(repo, tmp_path):
    ticket = repo.create(USER, URL)
    repo.enqueue(ticket.id, Quality.video(720))
    engine = StubEngine([TransientDownloadFailed("reset"), _asset(tmp_path, ticket.id)])

    workflow = _workflow(repo, tmp_path, engine, retries=3, backoff_seconds=0)
    await workflow.run(
        DownloadJob(ticket.id, USER, URL, Quality.video(720), attempts=1), RecordingDelivery()
    )

    assert repo.get(ticket.id).attempts == 3
