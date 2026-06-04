import pytest
import aiohttp
from aiohttp import web
from downloader import download_file, cleanup_file, DownloadError
from config import DOWNLOADS_DIR
import os

async def handle_download(request):
    return web.Response(text="test data" * 1000)

@pytest.fixture
async def mock_server(aiohttp_server):
    app = web.Application()
    app.router.add_get('/testfile', handle_download)
    return await aiohttp_server(app)

@pytest.mark.asyncio
async def test_successful_download(mock_server):
    url = f"http://{mock_server.host}:{mock_server.port}/testfile"
    file_path, size = await download_file(url, 999)

    assert os.path.exists(file_path)
    assert size > 0
    with open(file_path, 'r') as f:
        assert "test data" in f.read()

    cleanup_file(file_path)
    assert not os.path.exists(file_path)

@pytest.mark.asyncio
async def test_failed_download_404(mock_server):
    url = f"http://{mock_server.host}:{mock_server.port}/nonexistent"
    with pytest.raises(DownloadError):
        await download_file(url, 888)

@pytest.mark.asyncio
async def test_timeout_download():
    # Using a non-routable IP to cause timeout
    url = "http://10.255.255.1/test"
    # We need a very short timeout for testing or it will take 5 minutes
    import downloader
    from unittest.mock import patch

    with patch('downloader.DOWNLOAD_TIMEOUT', 1):
        with pytest.raises(DownloadError) as excinfo:
            await download_file(url, 777)
        assert "timed out" in str(excinfo.value).lower() or "network error" in str(excinfo.value).lower()
