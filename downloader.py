import os
import aiohttp
import asyncio
import logging
from typing import Optional, Tuple
from pathlib import Path
from config import CHUNK_SIZE, MAX_FILE_SIZE_MB, DOWNLOAD_TIMEOUT, DOWNLOADS_DIR

logger = logging.getLogger(__name__)

class DownloadError(Exception):
    pass

async def download_file(url: str, download_id: int) -> Tuple[str, int]:
    """
    Downloads a file from a URL using streaming.
    Returns (file_path, file_size).
    """
    timeout = aiohttp.ClientTimeout(total=DOWNLOAD_TIMEOUT)
    file_path: Optional[Path] = None
    try:
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.get(url, allow_redirects=True) as response:
                if response.status != 200:
                    raise DownloadError(f"Failed to download file: HTTP {response.status}")

                content_length = response.headers.get('Content-Length')
                if content_length and int(content_length) > MAX_FILE_SIZE_MB * 1024 * 1024:
                    raise DownloadError(f"File too large: {int(content_length) // (1024*1024)}MB > {MAX_FILE_SIZE_MB}MB")

                # Determine filename
                filename = "file_" + str(download_id)
                content_disposition = response.headers.get('Content-Disposition')
                if content_disposition and 'filename=' in content_disposition:
                    import re
                    match = re.findall('filename="?([^"]+)"?', content_disposition)
                    if match:
                        filename = match[0]
                else:
                    # Try to get from URL
                    path = Path(url).name
                    if path and '.' in path:
                        filename = path

                file_path = DOWNLOADS_DIR / f"{download_id}_{filename}"
                downloaded_size = 0

                with open(file_path, 'wb') as f:
                    async for chunk in response.content.iter_chunked(CHUNK_SIZE):
                        downloaded_size += len(chunk)
                        if downloaded_size > MAX_FILE_SIZE_MB * 1024 * 1024:
                            raise DownloadError(f"File exceeded maximum size during download.")
                        f.write(chunk)

                return str(file_path), downloaded_size
    except Exception as e:
        # Cleanup partial file on any exception
        if file_path and file_path.exists():
            try:
                file_path.unlink()
            except Exception as cleanup_err:
                logger.error(f"Failed to cleanup partial file {file_path}: {cleanup_err}")

        if isinstance(e, aiohttp.ClientError):
            raise DownloadError(f"Network error: {str(e)}")
        elif isinstance(e, asyncio.TimeoutError):
            raise DownloadError("Download timed out.")
        elif isinstance(e, DownloadError):
            raise e
        else:
            logger.exception("Unexpected error during download")
            raise DownloadError(f"Internal error: {str(e)}")

def cleanup_file(file_path: Optional[str]):
    if file_path:
        try:
            path = Path(file_path)
            if path.exists():
                path.unlink()
                logger.info(f"Cleaned up file: {file_path}")
        except Exception as e:
            logger.error(f"Error cleaning up file {file_path}: {e}")
