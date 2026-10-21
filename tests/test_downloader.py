import os

import pytest
from aiohttp import web

from config import DOWNLOADS_DIR
from downloader import (
    DownloadCancelled,
    DownloadError,
    DownloadResult,
    cleanup_file,
    download_file,
    is_instagram_url,
    is_media_url,
    is_youtube_url,
    redact_url,
)


async def handle_download(request):
    return web.Response(text="test data" * 1000)


async def handle_html(request):
    return web.Response(text="<html><body>video page</body></html>", content_type="text/html")


@pytest.fixture
async def mock_server(aiohttp_server):
    app = web.Application()
    app.router.add_get("/testfile", handle_download)
    app.router.add_get("/page", handle_html)
    return await aiohttp_server(app)


@pytest.mark.asyncio
async def test_successful_download(mock_server):
    url = f"http://{mock_server.host}:{mock_server.port}/testfile"
    result = await download_file(url, 999)
    assert isinstance(result, DownloadResult)
    path, size, title = result  # tuple unpacking must keep working
    assert os.path.exists(path) and size > 0 and title

    with open(path, "rb") as handle:
        assert b"test data" in handle.read()

    cleanup_file(path)
    assert not os.path.exists(path)


@pytest.mark.asyncio
async def test_failed_download_404(mock_server):
    url = f"http://{mock_server.host}:{mock_server.port}/nonexistent"
    with pytest.raises(DownloadError) as excinfo:
        await download_file(url, 888)
    assert "not found" in str(excinfo.value).lower()


@pytest.mark.asyncio
async def test_html_page_falls_back_to_extractor(mock_server):
    from unittest.mock import AsyncMock, patch

    url = f"http://{mock_server.host}:{mock_server.port}/page"
    expected = DownloadResult(path="/tmp/x.mp4", size=10, title="x", source="yt-dlp")
    with patch("downloader.download_with_ytdlp", new=AsyncMock(return_value=expected)) as fallback:
        result = await download_file(url, 5)
    fallback.assert_awaited_once()
    assert result is expected


@pytest.mark.asyncio
async def test_oversized_download_is_rejected(mock_server):
    from unittest.mock import patch

    url = f"http://{mock_server.host}:{mock_server.port}/testfile"
    with patch("downloader.MAX_BYTES", 10):
        with pytest.raises(DownloadError) as excinfo:
            await download_file(url, 4321)
    message = str(excinfo.value).lower()
    assert "can deliver at most" in message
    # nothing may be written for a file that can never be delivered
    import downloader as d
    assert not list(d.DOWNLOADS_DIR.glob("4321_*"))


@pytest.mark.asyncio
async def test_cancelled_before_start():
    import asyncio

    event = asyncio.Event()
    event.set()
    with pytest.raises(DownloadCancelled):
        await download_file("http://example.com/file.bin", 11, cancel_event=event)


def test_is_youtube_url():
    assert is_youtube_url("https://www.youtube.com/watch?v=dQw4w9WgXcQ")
    assert is_youtube_url("https://youtu.be/dQw4w9WgXcQ")
    assert is_youtube_url("https://www.youtube.com/embed/dQw4w9WgXcQ")
    assert is_youtube_url("https://www.youtube.com/shorts/dQw4w9WgXcQ")
    assert not is_youtube_url("https://example.com/file.mp4")


def test_is_instagram_url():
    assert is_instagram_url("https://www.instagram.com/p/C_abc123/")
    assert is_instagram_url("https://www.instagram.com/reels/C_abc123/")
    assert is_instagram_url("https://instagr.am/reel/C4p_8X9S-vV/")
    assert is_instagram_url("https://ig.me/reels/C4p_8X9S-vV/")
    assert not is_instagram_url("https://example.com/file.mp4")


def test_is_media_url():
    assert is_media_url("https://vm.tiktok.com/ZMabc/")
    assert is_media_url("https://x.com/user/status/123")
    assert not is_media_url("https://example.com/file.zip")


def test_redact_url_hides_query_strings():
    redacted = redact_url("https://cdn.example.com/a/b.mp4?token=SECRET")
    assert "SECRET" not in redacted
    assert "cdn.example.com" in redacted


def test_downloads_dir_exists():
    assert DOWNLOADS_DIR.exists()


def test_ytdlp_options_are_tuned(monkeypatch):
    import downloader as d

    monkeypatch.setattr(d, "YTDLP_PROXY", "socks5://127.0.0.1:9050")
    monkeypatch.setattr(d, "MAX_BYTES", 1234)
    opts = d._build_ytdlp_options(5, {"format": "best"})

    assert opts["format"] == "best"
    assert opts["proxy"] == "socks5://127.0.0.1:9050"
    assert opts["max_filesize"] == 1234
    assert opts["noplaylist"] is True
    assert opts["concurrent_fragment_downloads"] >= 1
    assert opts["merge_output_format"] == "mp4"


def test_ytdlp_options_respect_cookie_file(monkeypatch, tmp_path):
    import downloader as d

    cookies = tmp_path / "cookies.txt"
    cookies.write_text("# Netscape HTTP Cookie File\n")
    monkeypatch.setattr(d, "YTDLP_COOKIES_FILE", str(cookies))
    opts = d._build_ytdlp_options(6)
    assert opts["cookiefile"] == str(cookies)


def test_filename_from_disposition():
    from downloader import _filename_from_disposition

    assert _filename_from_disposition('attachment; filename="report.pdf"') == "report.pdf"
    assert (
        _filename_from_disposition("attachment; filename*=UTF-8''na%C3%AFve.txt")
        == "naïve.txt"
    )
    assert _filename_from_disposition("") is None


def test_partial_streams_are_swept(tmp_path, monkeypatch):
    """A merged download must not leave video/audio streams behind."""
    import downloader as d

    keep = d.DOWNLOADS_DIR / "77_movie.mp4"
    keep.write_bytes(b"final")
    leftovers = [
        d.DOWNLOADS_DIR / "77_movie.f137.mp4",
        d.DOWNLOADS_DIR / "77_movie.f140.m4a",
        d.DOWNLOADS_DIR / "77_movie.mp4.part",
    ]
    for path in leftovers:
        path.write_bytes(b"x")

    d._cleanup_parts(77, keep=keep)

    assert keep.exists()
    assert not any(path.exists() for path in leftovers)
