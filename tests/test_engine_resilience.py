"""Failure classification, retries and stall handling of the engine."""

from __future__ import annotations

import asyncio

import pytest
from aiohttp import web

from linktofile.domain.errors import BotError, TransientDownloadFailed
from linktofile.infrastructure.download.engine import (
    DownloadError,
    StalledDownload,
    classify_ytdlp_failure,
    download_file,
    _classify_http_status,
)
from linktofile.shared import security


def test_429_and_5xx_are_transient():
    for status in (429, 408, 425, 500, 502, 503, 599):
        classified = _classify_http_status(status)
        assert isinstance(classified, TransientDownloadFailed), status


def test_private_and_missing_files_fail_fast():
    for status in (401, 403, 404, 400, 418):
        classified = _classify_http_status(status)
        assert isinstance(classified, DownloadError)
        assert not isinstance(classified, TransientDownloadFailed), status


def test_ytdlp_hints_map_to_failure_kinds():
    assert "larger" in str(classify_ytdlp_failure("file is larger than max-filesize limit"))
    assert "private" in str(classify_ytdlp_failure("This video is private")).lower()
    assert "login" in str(classify_ytdlp_failure("Please sign in to continue")).lower()
    assert "bot" in str(classify_ytdlp_failure("Confirm you're not a bot")).lower()

    throttled = classify_ytdlp_failure("HTTP Error 429: Too Many Requests", host="example.com")
    assert isinstance(throttled, TransientDownloadFailed)

    connection = classify_ytdlp_failure("Connection reset by peer", host="example.com")
    assert isinstance(connection, TransientDownloadFailed)


def test_media_unavailable_beats_login_required():
    classified = classify_ytdlp_failure(
        "login required because the media is unavailable", host="example.com"
    )
    assert "unavailable" in str(classified).lower()
    assert "removed" in str(classified).lower()


def test_stall_and_transient_kinds_are_recorded_distinctly():
    from linktofile.domain.models import FailureKind, failure_kind_of

    assert failure_kind_of(StalledDownload("stalled")) == FailureKind.STALLED.value
    assert failure_kind_of(TransientDownloadFailed("reset")) == FailureKind.NETWORK.value
    assert failure_kind_of(BotError("anything else")) == FailureKind.UNKNOWN.value


@pytest.fixture(autouse=True)
def _allow_the_local_test_server():
    security.set_enforcement(False)
    yield
    security.set_enforcement(None)


async def test_a_stalled_transfer_times_out_instead_of_hanging(aiohttp_server, monkeypatch):
    monkeypatch.setattr(
        "linktofile.infrastructure.download.engine.STALL_TIMEOUT", 0.3
    )

    async def drip(request):
        response = web.StreamResponse(
            headers={
                "Content-Type": "application/octet-stream",
                "Content-Disposition": 'attachment; filename="drip.bin"',
            }
        )
        await response.prepare(request)
        await response.write(b"x")
        await asyncio.sleep(3.0)  # silent after the first chunk -> stalled
        await response.write(b"y")
        return response

    app = web.Application()
    app.router.add_get("/slow.bin", drip)
    slow_server = await aiohttp_server(app)

    with pytest.raises(StalledDownload):
        await download_file(str(slow_server.make_url("/slow.bin")), 4242)


async def test_a_redirect_to_a_private_target_is_refused(aiohttp_server):
    """Hop-by-hop validation: even a 302 onto loopback is blocked."""
    security.set_enforcement(True)
    try:

        async def bounce(request):
            raise web.HTTPFound("/secret.bin")

        app = web.Application()
        app.router.add_get("/bounce", bounce)
        redirector = await aiohttp_server(app)

        with pytest.raises(BotError):
            await download_file(str(redirector.make_url("/bounce")), 4243)
    finally:
        security.set_enforcement(None)
