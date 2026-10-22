"""Admin observability: queue depth, stuck jobs, real failure kinds, resources."""

from __future__ import annotations

import time

from linktofile.domain.models import DownloadStatus, FailureKind, Quality
from linktofile.infrastructure.persistence.sqlite import (
    SqliteStats,
    SqliteTickets,
    SqliteUsers,
)
from linktofile.infrastructure.system.metrics import directory_bytes, rss_mb, uptime_text
from linktofile.infrastructure.telegram import texts

USER = 7


def test_the_dashboard_reports_queue_depth_and_resources():
    policy = type(
        "P", (), {"enabled": False, "channel": "", "is_active": False}
    )()
    stats = {
        "users": 3,
        "total": 10,
        "today": 4,
        "completed": 7,
        "failed": 2,
        "bytes": 5 * 1024 * 1024,
        "queued": 2,
        "downloading": 1,
        "uploading": 1,
        "errors": 2,
    }

    text = texts.admin_text(
        policy,
        stats,
        active=1,
        pending=2,
        disk_free="900 MB",
        memory_mb=210,
        temp_used="1.5 MB",
        uptime="2h 5m",
        failures_today=2,
        resumed=1,
    )

    assert "2</b> queued" in text
    assert "1</b> downloading" in text
    assert "210 MB" in text
    assert "2h 5m" in text
    assert "1.5 MB" in text
    assert "Resumed this boot" in text


def test_the_queue_view_lists_live_jobs_with_age(database):
    users = SqliteUsers(database)
    users.register(USER)
    tickets = SqliteTickets(database)
    running = tickets.create(USER, "https://example.com/one.mp4")
    tickets.enqueue(running.id, Quality.video(720))
    tickets.update(running.id, DownloadStatus.DOWNLOADING)

    text = texts.admin_queue_text(
        tickets.queue_contents(), live={running.id: "running"}
    )

    assert f"#{running.id}" in text
    assert "example.com" in text
    assert "running" in text
    assert "720p" in text


def test_an_empty_queue_says_so():
    assert "Nothing is queued" in texts.admin_queue_text([])


def test_the_error_view_shows_the_failure_kind(database):
    users = SqliteUsers(database)
    users.register(USER)
    tickets = SqliteTickets(database)
    broken = tickets.create(USER, "https://example.com/broken.mp4")
    tickets.fail(broken.id, kind=FailureKind.STALLED, error="Nothing arrived for 120s")

    text = texts.admin_errors_text(tickets.recent_failures())

    assert FailureKind.STALLED.value in text
    assert "Nothing arrived" in text


def test_the_error_view_handles_a_clean_run():
    assert "No failed tickets" in texts.admin_errors_text([])


def test_stats_expose_queue_columns(database):
    users = SqliteUsers(database)
    users.register(USER)
    tickets = SqliteTickets(database)
    one = tickets.create(USER, "https://example.com/a.mp4")
    two = tickets.create(USER, "https://example.com/b.mp4")
    tickets.enqueue(one.id, Quality.video(720))
    tickets.enqueue(two.id, Quality.video(720))
    tickets.update(two.id, DownloadStatus.UPLOADING)

    stats = SqliteStats(database).global_()

    assert stats["queued"] == 1
    assert stats["uploading"] == 1
    assert stats["errors"] == 0


def test_metrics_are_cheap_and_safe(tmp_path):
    (tmp_path / "a.bin").write_bytes(b"x" * 100)
    (tmp_path / "b.bin").write_bytes(b"y" * 50)
    assert directory_bytes(tmp_path) == 150
    assert directory_bytes(tmp_path / "missing") == 0
    assert rss_mb() >= 0
    assert uptime_text(time.monotonic() + 90).endswith("m")
    assert uptime_text(time.monotonic() + 3).endswith("s")
