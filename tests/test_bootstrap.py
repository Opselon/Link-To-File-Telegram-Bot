"""Composition root smoke test: wiring mistakes must fail here, not in production."""

from __future__ import annotations

import pytest

from linktofile import bootstrap


@pytest.fixture
def wired(monkeypatch, tmp_path):
    from linktofile.infrastructure.telegram import handlers

    # The handler router is module-level, so a second Dispatcher in the same
    # process needs it detached first (production builds exactly one).
    handlers.router._parent_router = None
    monkeypatch.setattr(bootstrap, "BOT_TOKEN", "123456:TEST-TOKEN")
    monkeypatch.setattr(bootstrap, "DB_PATH", tmp_path / "bot.db")
    monkeypatch.setattr(bootstrap, "DOWNLOADS_DIR", tmp_path)
    app = bootstrap.build_application()
    yield app


async def test_every_layer_is_wired(wired):
    services = wired.services
    assert services.queue.concurrency >= 1
    assert services.workflow.temp_dir.exists()
    assert services.policy.cap_mb >= 1
    assert services.version
    # The dispatcher knows about exactly one core router.
    assert wired.dispatcher.sub_routers
    await wired.close()


async def test_startup_and_shutdown_are_clean(wired):
    from linktofile.infrastructure.telegram.app import on_shutdown, on_startup

    await on_startup(wired.services, wired.bot)
    assert wired.services.queue.active == 0
    await on_shutdown(wired.services, wired.bot)
    await wired.close()
