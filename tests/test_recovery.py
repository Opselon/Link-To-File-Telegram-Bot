"""Crash recovery: tickets left in flight by a previous process are reaped."""

from __future__ import annotations

import pytest

from linktofile.domain.models import DownloadStatus
from linktofile.infrastructure.persistence.sqlite import (
    SqliteTickets,
    SqliteUsers,
)


@pytest.fixture
def wired_app(monkeypatch, tmp_path):
    """A fully wired application on a throwaway database (same shape as prod)."""
    from linktofile import bootstrap
    from linktofile.infrastructure.telegram import handlers

    handlers.router._parent_router = None
    monkeypatch.setattr(bootstrap, "BOT_TOKEN", "123456:TEST-TOKEN")
    monkeypatch.setattr(bootstrap, "DB_PATH", tmp_path / "bot.db")
    monkeypatch.setattr(bootstrap, "DOWNLOADS_DIR", tmp_path)
    return bootstrap.build_application()


def test_reap_stale_only_touches_unfinished_tickets(database):
    users, tickets = SqliteUsers(database), SqliteTickets(database)
    users.register(7)

    pending = tickets.create(7, "https://example.com/queued.zip")
    running = tickets.create(7, "https://example.com/running.zip")
    uploading = tickets.create(7, "https://example.com/uploading.zip")
    done = tickets.create(7, "https://example.com/done.zip")
    tickets.update(running.id, DownloadStatus.DOWNLOADING)
    tickets.update(uploading.id, DownloadStatus.UPLOADING)
    tickets.update(done.id, DownloadStatus.COMPLETED, filename="done.zip", size=10)

    assert tickets.reap_stale() == 3
    assert tickets.get(pending.id).status is DownloadStatus.FAILED
    assert tickets.get(running.id).status is DownloadStatus.FAILED
    assert tickets.get(uploading.id).status is DownloadStatus.FAILED
    assert tickets.get(done.id).status is DownloadStatus.COMPLETED


def test_reap_stale_is_idempotent(database):
    users, tickets = SqliteUsers(database), SqliteTickets(database)
    users.register(7)
    tickets.create(7, "https://example.com/queued.zip")

    assert tickets.reap_stale() == 1
    assert tickets.reap_stale() == 0


async def test_the_bot_reaps_on_startup(wired_app):
    from linktofile.infrastructure.telegram.app import on_shutdown, on_startup

    tickets = wired_app.services.tickets
    wired_app.services.users.register(7)
    ticket = tickets.create(7, "https://example.com/stuck.zip")
    tickets.update(ticket.id, DownloadStatus.DOWNLOADING)

    await on_startup(wired_app.services, wired_app.bot)

    assert tickets.get(ticket.id).status is DownloadStatus.FAILED
    await on_shutdown(wired_app.services, wired_app.bot)
    await wired_app.close()
