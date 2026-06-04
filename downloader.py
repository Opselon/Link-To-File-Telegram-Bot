import os
import aiohttp
import asyncio
import logging
import yt_dlp
import re
from typing import Optional, Tuple, Callable, Any
from pathlib import Path
from urllib.parse import urlparse
from config import CHUNK_SIZE, MAX_FILE_SIZE_MB, DOWNLOAD_TIMEOUT, DOWNLOADS_DIR
from utils import strip_ansi

logger = logging.getLogger(__name__)

class DownloadError(Exception):
    pass

def is_youtube_url(url: str) -> bool:
    youtube_regex = (
        r'(https?://)?(www\.)?'
        r'(youtube|youtu|youtube-nocookie)\.(com|be)/'
        r'(watch\?v=|embed/|v/|.+\?v=)?([^&=%\?]{11})'
    )
    return re.match(youtube_regex, url) is not None

def is_instagram_url(url: str) -> bool:
    """Checks if the URL is an Instagram URL."""
    instagram_regex = (
        r'(https?://)?(www\.)?(instagram\.com|instagr\.am|ig\.me)/(p|reels|reel|stories|tv|s|sh)/[^/?#&]+'
    )
    return re.match(instagram_regex, url) is not None

async def download_with_ytdlp(url: str, download_id: int, options: Optional[dict] = None, progress_callback: Optional[Callable] = None) -> Tuple[str, int, str]:
    ydl_opts = {
        'format': 'best',
        'outtmpl': str(DOWNLOADS_DIR / f"{download_id}_%(title)s.%(ext)s"),
        'max_filesize': MAX_FILE_SIZE_MB * 1024 * 1024,
        'quiet': True,
        'no_warnings': True,
        'nocheckcertificate': True,
        'no_color': True,
        'ignoreerrors': False,
        'logtostderr': False,
        'concurrent_fragment_downloads': 10,
        'headers': {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36',
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
            'Accept-Language': 'en-US,en;q=0.5',
            'Referer': 'https://www.google.com/',
        }
    }
    if options:
        ydl_opts.update(options)

    if progress_callback:
        def ytdlp_hook(d):
            if d['status'] == 'downloading':
                progress_callback(
                    d.get('downloaded_bytes', 0),
                    d.get('total_bytes') or d.get('total_bytes_estimate', 0),
                    d.get('speed', 0),
                    d.get('eta', 0)
                )
        ydl_opts['progress_hooks'] = [ytdlp_hook]

    try:
        loop = asyncio.get_event_loop()
        # Run yt-dlp in a thread pool as it is blocking
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = await loop.run_in_executor(None, lambda: ydl.extract_info(url, download=True))
            if 'entries' in info:
                info = info['entries'][0]

            file_path = ydl.prepare_filename(info)
            title = info.get('title', 'Video')
            # yt-dlp might change extension if it post-processes (e.g. to mp3)
            if not os.path.exists(file_path):
                # Search for files starting with download_id
                possible_files = list(DOWNLOADS_DIR.glob(f"{download_id}_*"))
                if possible_files:
                    # Sort by mtime to get the most recent one
                    file_path = str(sorted(possible_files, key=os.path.getmtime)[-1])
                else:
                    raise DownloadError("Could not find downloaded file.")

            file_size = os.path.getsize(file_path)
            return str(file_path), file_size, title
    except Exception as e:
        error_msg = strip_ansi(str(e))
        logger.error(f"Media download error: {error_msg}")

        if "File is larger than max_filesize" in error_msg:
            raise DownloadError(f"File too large: exceeds {MAX_FILE_SIZE_MB}MB")

        if "ffprobe and ffmpeg not found" in error_msg or "ffmpeg is not installed" in error_msg:
            raise DownloadError("Download failed: FFmpeg is not installed on the server. Please contact the administrator.")

        raise DownloadError(f"Download failed: {error_msg}")

async def download_file(url: str, download_id: int, ytdlp_options: Optional[dict] = None, progress_callback: Optional[Callable] = None) -> Tuple[str, int, str]:
    """
    Downloads a file from a URL using streaming or yt-dlp for YouTube/Instagram.
    Returns (file_path, file_size, title).
    """
    if is_youtube_url(url) or is_instagram_url(url):
        return await download_with_ytdlp(url, download_id, ytdlp_options, progress_callback)

    timeout = aiohttp.ClientTimeout(total=DOWNLOAD_TIMEOUT)
    file_path: Optional[Path] = None
    max_retries = 3
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
    }

    for attempt in range(max_retries):
        try:
            async with aiohttp.ClientSession(timeout=timeout, headers=headers) as session:
                async with session.get(url, allow_redirects=True) as response:
                    if response.status != 200:
                        raise DownloadError(f"Failed to download file: HTTP {response.status}")

                    content_length = response.headers.get('Content-Length')
                    if content_length and int(content_length) > MAX_FILE_SIZE_MB * 1024 * 1024:
                        raise DownloadError(f"File too large: {int(content_length) // (1024*1024)}MB > {MAX_FILE_SIZE_MB}MB")

                    # Determine filename
                    filename = "file_" + str(download_id)
                    title = "File"
                    content_disposition = response.headers.get('Content-Disposition')
                    if content_disposition and 'filename=' in content_disposition:
                        match = re.findall('filename="?([^"]+)"?', content_disposition)
                        if match:
                            filename = match[0]
                            title = filename
                    else:
                        # Try to get from URL
                        parsed_url = Path(urlparse(url).path)
                        path_name = parsed_url.name
                        if path_name and '.' in path_name:
                            filename = path_name
                            title = path_name

                    file_path = DOWNLOADS_DIR / f"{download_id}_{filename}"
                    downloaded_size = 0

                    total_size = int(content_length) if content_length else 0
                    start_time = asyncio.get_event_loop().time()

                    with open(file_path, 'wb') as f:
                        async for chunk in response.content.iter_chunked(CHUNK_SIZE):
                            downloaded_size += len(chunk)
                            if downloaded_size > MAX_FILE_SIZE_MB * 1024 * 1024:
                                raise DownloadError(f"File exceeded maximum size during download.")
                            f.write(chunk)

                            if progress_callback:
                                current_time = asyncio.get_event_loop().time()
                                elapsed = current_time - start_time
                                speed = downloaded_size / elapsed if elapsed > 0 else 0
                                eta = (total_size - downloaded_size) / speed if speed > 0 and total_size > 0 else 0
                                progress_callback(downloaded_size, total_size, speed, eta)

                    return str(file_path), downloaded_size, title
        except (aiohttp.ClientError, asyncio.TimeoutError) as e:
            if attempt < max_retries - 1:
                logger.warning(f"Download attempt {attempt+1} failed: {e}. Retrying...")
                await asyncio.sleep(2 ** attempt)
                continue
            else:
                if isinstance(e, aiohttp.ClientError):
                    raise DownloadError(f"Network error: {str(e)}")
                else:
                    raise DownloadError("Download timed out after multiple attempts.")
        except Exception as e:
            # Cleanup partial file on any exception
            if file_path and file_path.exists():
                try:
                    file_path.unlink()
                except Exception as cleanup_err:
                    logger.error(f"Failed to cleanup partial file {file_path}: {cleanup_err}")

            if isinstance(e, DownloadError):
                raise e
            else:
                logger.exception("Unexpected error during download")
                raise DownloadError(f"Internal error: {str(e)}")
    return "", 0 # Should not reach here

def cleanup_file(file_path: Optional[str]):
    if file_path:
        try:
            path = Path(file_path)
            if path.exists():
                path.unlink()
                logger.info(f"Cleaned up file: {file_path}")
        except Exception as e:
            logger.error(f"Error cleaning up file {file_path}: {e}")
