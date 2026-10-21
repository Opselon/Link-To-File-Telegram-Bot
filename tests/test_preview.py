"""The preview service: cached, deadline-bound, and never fatal."""

from __future__ import annotations

import asyncio
import time

from linktofile.application.preview import PreviewService
from linktofile.domain.models import MediaInfo

INFO = MediaInfo(
    title="A short clip",
    uploader="Ada",
    duration=95,
    thumbnail="https://cdn.example/t.jpg",
    site="youtu.be",
    url="https://youtu.be/x",
)


class FakeProbe:
    def __init__(self, info=None, *, delay: float = 0.0, error=None) -> None:
        self.info = info
        self.delay = delay
        self.error = error
        self.calls = 0

    async def probe(self, url: str):
        self.calls += 1
        if self.delay:
            await asyncio.sleep(self.delay)
        if self.error:
            raise self.error
        return self.info


def test_media_info_only_truster_http_thumbnails():
    assert MediaInfo(title="x", thumbnail="https://cdn/x.jpg").has_thumbnail
    assert MediaInfo(title="x", thumbnail="http://cdn/x.jpg").has_thumbnail
    assert not MediaInfo(title="x").has_thumbnail
    assert not MediaInfo(title="x", thumbnail="ftp://cdn/x.jpg").has_thumbnail
    assert not MediaInfo(title="x", thumbnail="file:///etc/passwd").has_thumbnail


async def test_describe_probes_once_then_serves_from_cache():
    probe = FakeProbe(INFO)
    service = PreviewService(probe, timeout=5, ttl=60)

    first = await service.describe("https://youtu.be/x")
    second = await service.describe("https://youtu.be/x")

    assert first == INFO and second == INFO
    assert probe.calls == 1
    assert service.cache_size == 1


async def test_describe_gives_up_on_a_slow_probe():
    service = PreviewService(FakeProbe(INFO, delay=2), timeout=0.05, ttl=60)

    started = time.monotonic()
    assert await service.describe("https://youtu.be/x") is None
    assert time.monotonic() - started < 1.0


async def test_describe_survives_a_failing_probe():
    service = PreviewService(FakeProbe(error=RuntimeError("boom")), timeout=5, ttl=60)

    assert await service.describe("https://youtu.be/x") is None
    # The failure is not cached, so the next attempt really probes again.
    assert await service.describe("https://youtu.be/x") is None


async def test_describe_passes_none_through():
    service = PreviewService(FakeProbe(None), timeout=5, ttl=60)
    assert await service.describe("https://youtu.be/x") is None
    assert service.cache_size == 0


async def test_cache_expires_after_the_ttl():
    probe = FakeProbe(INFO)
    service = PreviewService(probe, timeout=5, ttl=0.01)

    await service.describe("https://youtu.be/x")
    await asyncio.sleep(0.05)
    await service.describe("https://youtu.be/x")

    assert probe.calls == 2


async def test_the_cache_stays_bounded():
    service = PreviewService(FakeProbe(INFO), timeout=5, ttl=60, capacity=3)

    for index in range(20):
        await service.describe(f"https://youtu.be/{index}")

    assert service.cache_size <= 3


async def test_forget_drops_one_entry_or_all():
    service = PreviewService(FakeProbe(INFO), timeout=5, ttl=60)
    await service.describe("https://youtu.be/a")
    await service.describe("https://youtu.be/b")

    service.forget("https://youtu.be/a")
    assert service.cache_size == 1
    service.forget()
    assert service.cache_size == 0
