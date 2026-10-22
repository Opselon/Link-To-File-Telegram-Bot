"""The durable queue: job state lives in SQLite, not in memory.

Covers the contract the restart path depends on — a job committed before the
in-memory submit, one live job per (user, url), and a recovery that tells
"still queued" apart from "was running when we died".
"""

from __future__ import annotations

import pytest

from linktofile.domain.errors import DuplicateJob
from linktofile.domain.models import DownloadStatus, FailureKind, Quality
from linktofile.infrastructure.persistence.sqlite import (
    Database,
    SqliteStats,
    SqliteTickets,
    SqliteUsers,
)

URL = "https://example.com/video.mp4"


@pytest.fixture
def repo(database):
    users = SqliteUsers(database)
    users.register(7)
    users.register(8)
    return SqliteTickets(database)


def test_enqueue_commits_the_job_and_its_quality(repo):
    ticket = repo.create(7, URL)

    repo.enqueue(ticket.id, Quality.video(720))

    stored = repo.get(ticket.id)
    assert stored.status is DownloadStatus.QUEUED
    assert stored.quality == Quality.video(720)


def test_a_second_live_job_for_the_same_link_is_refused(repo):
    first = repo.create(7, URL)
    repo.enqueue(first.id, Quality.video(720))

    second = repo.create(7, URL)
    with pytest.raises(DuplicateJob):
        repo.enqueue(second.id, Quality.video(480))

    assert repo.get(second.id).status is not DownloadStatus.QUEUED
    assert repo.active_duplicate(7, URL).id == first.id


def test_the_same_link_for_another_user_is_allowed(repo):
    mine = repo.create(7, URL)
    theirs = repo.create(8, URL)

    repo.enqueue(mine.id, Quality.audio(192))
    repo.enqueue(theirs.id, Quality.audio(192))

    assert repo.get(mine.id).status is DownloadStatus.QUEUED
    assert repo.get(theirs.id).status is DownloadStatus.QUEUED


def test_cancelling_releases_the_dedupe_slot(repo):
    first = repo.create(7, URL)
    repo.enqueue(first.id, Quality.video(720))
    assert repo.cancel(first.id) is True

    again = repo.create(7, URL)
    repo.enqueue(again.id, Quality.video(720))

    assert repo.get(again.id).status is DownloadStatus.QUEUED


def test_queued_jobs_survive_a_restart(repo, database):
    ticket = repo.create(7, URL)
    repo.enqueue(ticket.id, Quality.audio(320))

    # A brand new process opens the same file.
    reopened = SqliteTickets(Database(database.db_path))
    report = reopened.recover_after_restart()

    assert [job.ticket_id for job in report.resumable] == [ticket.id]
    resumed = report.resumable[0]
    assert resumed.url == URL
    assert resumed.user_id == 7
    assert resumed.quality == Quality.audio(320)


def test_a_running_job_becomes_interrupted_not_resumed(repo):
    ticket = repo.create(7, URL)
    repo.enqueue(ticket.id, Quality.video(720))
    repo.update(ticket.id, DownloadStatus.DOWNLOADING)

    report = repo.recover_after_restart()

    assert report.resumable == []
    assert report.interrupted == 1
    stored = repo.get(ticket.id)
    assert stored.status is DownloadStatus.FAILED
    assert stored.error_kind == FailureKind.INTERRUPTED.value


def test_an_abandoned_picker_is_not_resumed(repo):
    ticket = repo.create(7, "https://example.com/never-picked.mp4")

    report = repo.recover_after_restart()

    assert report.resumable == []
    assert report.abandoned == 1
    stored = repo.get(ticket.id)
    assert stored.status is DownloadStatus.FAILED
    assert stored.error_kind == FailureKind.ABANDONED.value


def test_a_queued_row_without_a_parsable_quality_is_failed(repo, database):
    ticket = repo.create(7, URL)
    repo.enqueue(ticket.id, Quality.video(720))
    with database.connect() as conn:
        conn.execute("UPDATE downloads SET quality = 'garbage' WHERE id = ?", (ticket.id,))
        conn.commit()

    report = repo.recover_after_restart()

    assert report.resumable == []
    assert repo.get(ticket.id).status is DownloadStatus.FAILED


def test_cancelled_jobs_are_not_resumed(repo):
    ticket = repo.create(7, URL)
    repo.enqueue(ticket.id, Quality.video(720))
    repo.cancel(ticket.id)

    report = repo.recover_after_restart()

    assert report.resumable == []
    assert report.interrupted == 0
    assert repo.get(ticket.id).status is DownloadStatus.CANCELLED


def test_recovery_is_idempotent(repo):
    ticket = repo.create(7, URL)
    repo.enqueue(ticket.id, Quality.video(720))

    first = repo.recover_after_restart()
    second = repo.recover_after_restart()

    assert len(first.resumable) == 1
    assert len(second.resumable) == 1  # still queued until a worker claims it


def test_the_admin_views_see_only_live_and_failed_rows(repo):
    queued = repo.create(7, URL)
    repo.enqueue(queued.id, Quality.video(720))
    done = repo.create(7, "https://example.com/done.mp4")
    repo.update(done.id, DownloadStatus.COMPLETED, size=10)
    broken = repo.create(7, "https://example.com/broken.mp4")
    repo.fail(broken.id, kind=FailureKind.NETWORK, error="connection reset")

    assert [t.id for t in repo.queue_contents()] == [queued.id]
    [failure] = repo.recent_failures()
    assert failure.id == broken.id
    assert failure.error_kind == FailureKind.NETWORK.value
    assert repo.counts_by_status()["queued"] == 1


def test_stats_report_queue_depth(repo, database):
    live = repo.create(7, URL)
    repo.enqueue(live.id, Quality.video(720))
    repo.update(live.id, DownloadStatus.DOWNLOADING)

    stats = SqliteStats(database).global_()

    assert stats["queued"] == 0
    assert stats["downloading"] == 1
    assert stats["total"] == 1


def test_quality_round_trips_through_the_row(repo):
    for quality in (Quality.video(720), Quality.video(None), Quality.audio(192)):
        ticket = repo.create(7, f"https://example.com/{quality.label}.mp4")
        repo.enqueue(ticket.id, quality)
        assert repo.get(ticket.id).quality == quality
