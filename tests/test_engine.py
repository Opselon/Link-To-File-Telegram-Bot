"""The download engine against a real local HTTP server (no external network)."""

import asyncio
from pathlib import Path

import pytest
from aiohttp import web

from linktofile.config import DOWNLOADS_DIR, UPLOAD_LIMIT_BYTES, UPLOAD_LIMIT_MB
from linktofile.infrastructure.download.engine import (
    DownloadCancelled,
    DownloadError,
    _build_ytdlp_options,
    _filename_from_disposition,
    cleanup_file,
    download_file,
    is_media_url,
)


@pytest.fixture
async def server(aiohttp_server):
    async def report(request):
        return web.Response(
            body=b"a" * 2048,
            headers={
                "Content-Type": "text/plain",
                "Content-Disposition": 'attachment; filename="report.txt"',
            },
        )

    async def huge(request):
        return web.Response(
            body=b"b",
            headers={"Content-Type": "application/octet-stream", "Content-Length": "999999999"},
        )

    app = web.Application()
    app.router.add_get("/report.txt", report)
    app.router.add_get("/huge.bin", huge)
    return await aiohttp_server(app)


async def test_direct_download_writes_the_file(server):
    url = str(server.make_url("/report.txt"))
    result = await download_file(url, 9001)

    path = Path(result.path)
    assert path.exists()
    assert path.name == "9001_report.txt"
    assert path.stat().st_size == 2048
    assert result.size == 2048
    assert result.source == "direct"

    cleanup_file(result.path)
    assert not path.exists()


async def test_oversized_file_is_refused_before_writing(server):
    before = set(DOWNLOADS_DIR.glob("9002_*"))
    with pytest.raises(DownloadError) as excinfo:
        await download_file(str(server.make_url("/huge.bin")), 9002)

    assert "deliver at most" in str(excinfo.value)
    assert set(DOWNLOADS_DIR.glob("9002_*")) == before


async def test_cancel_event_stops_before_the_request(server):
    event = asyncio.Event()
    event.set()
    with pytest.raises(DownloadCancelled):
        await download_file(str(server.make_url("/report.txt")), 9003, cancel_event=event)


def test_options_respect_the_delivery_cap():
    options = _build_ytdlp_options(1)
    assert options["max_filesize"] == UPLOAD_LIMIT_BYTES
    assert options["noplaylist"] is True
    assert options["concurrent_fragment_downloads"] >= 1
    assert options["merge_output_format"] == "mp4"
    assert options["outtmpl"].startswith(str(DOWNLOADS_DIR))
    assert "progress_hooks" not in options


def test_options_add_a_progress_hook_when_asked():
    options = _build_ytdlp_options(1, progress_callback=lambda *a: None)
    assert len(options["progress_hooks"]) == 1


def test_options_merge_quality_overrides():
    options = _build_ytdlp_options(1, {"format": "b[height<=480]"})
    assert options["format"] == "b[height<=480]"


def test_filename_from_disposition_variants():
    assert _filename_from_disposition('attachment; filename="a.mp4"') == "a.mp4"
    assert _filename_from_disposition("attachment; filename*=UTF-8''na%20me.mp4") == "na me.mp4"
    assert _filename_from_disposition("") is None


def test_media_detection_matches_the_engine():
    assert is_media_url("https://youtu.be/x")
    assert not is_media_url("https://example.com/a.zip")
    assert UPLOAD_LIMIT_MB >= 1
