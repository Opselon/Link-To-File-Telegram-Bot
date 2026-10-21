"""Download engine: direct HTTP streaming plus yt-dlp for media pages."""

from __future__ import annotations

import asyncio
import logging
import os
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Optional
from urllib.parse import unquote, urlparse

import aiohttp

from linktofile.config import (
    CHUNK_SIZE,
    DOWNLOAD_TIMEOUT,
    DOWNLOADS_DIR,
    HTTP_POOL_SIZE,
    UPLOAD_LIMIT_BYTES,
    PROXY_URL,
    YTDLP_CACHE_DIR,
    YTDLP_COOKIES_FILE,
    YTDLP_FRAGMENTS,
    YTDLP_PROXY,
)
from linktofile.shared.utils import clean_filename, format_size, strip_ansi

logger = logging.getLogger(__name__)

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)

MAX_BYTES = UPLOAD_LIMIT_BYTES
_PROGRESS_THROTTLE = 0.4


class DownloadError(Exception):
    """A user-presentable download failure."""


class DownloadCancelled(DownloadError):
    def __init__(self, message: str = "Download cancelled."):
        super().__init__(message)


class _NeedsExtractor(Exception):
    """The URL is a web page, not a file: hand it to yt-dlp."""


@dataclass(slots=True)
class DownloadResult:
    path: str
    size: int
    title: str
    duration: Optional[int] = None
    width: Optional[int] = None
    height: Optional[int] = None
    artist: Optional[str] = None
    source: str = "direct"

    def __iter__(self):
        """Allow ``path, size, title = result`` (kept for API stability)."""
        yield self.path
        yield self.size
        yield self.title


# --- HTTP session -----------------------------------------------------------
_session: Optional[aiohttp.ClientSession] = None
_session_loop: Optional[asyncio.AbstractEventLoop] = None


async def get_session() -> aiohttp.ClientSession:
    """One pooled session for every download (keep-alive + DNS cache).

    Session creation is synchronous, so this needs no lock: the coroutine never
    yields between the check and the assignment.
    """
    global _session, _session_loop
    loop = asyncio.get_running_loop()
    if _session is not None and not _session.closed and _session_loop is loop:
        return _session
    if _session is not None and not _session.closed and _session_loop is not loop:
        # A session is bound to the loop that created it (tests and reloads).
        try:
            result = _session._connector.close()  # type: ignore[attr-defined]
            if asyncio.iscoroutine(result):
                result.close()  # never awaited: the old loop is gone
        except Exception:  # noqa: BLE001
            pass
        _session = None
    if _session is None or _session.closed:
        kwargs: dict[str, Any] = {
            "timeout": aiohttp.ClientTimeout(total=None, sock_connect=30),
            "headers": {"User-Agent": USER_AGENT, "Accept-Encoding": "identity"},
        }
        if PROXY_URL and PROXY_URL.lower().startswith(("socks4", "socks5")):
            from aiohttp_socks import ProxyConnector

            kwargs["connector"] = ProxyConnector.from_url(PROXY_URL, limit=HTTP_POOL_SIZE)
        else:
            kwargs["connector"] = aiohttp.TCPConnector(
                limit=HTTP_POOL_SIZE, ttl_dns_cache=300
            )
        _session = aiohttp.ClientSession(**kwargs)
        _session_loop = loop
    return _session


async def close_session() -> None:
    global _session, _session_loop
    if _session is not None and not _session.closed:
        await _session.close()
    _session = None
    _session_loop = None


# --- URL helpers ------------------------------------------------------------
_YOUTUBE_RE = re.compile(
    r"(?:https?://)?(?:www\.|m\.)?"
    r"(?:youtube\.com|youtu\.be|youtube-nocookie\.com)/"
    r"(?:watch\?v=|embed/|v/|shorts/|live/|.+[?&]v=)?([\w-]{11})",
    re.IGNORECASE,
)
_INSTAGRAM_RE = re.compile(
    r"(?:https?://)?(?:www\.)?(?:instagram\.com|instagr\.am|ig\.me)/"
    r"(?:p|reels?|stories|tv|s|sh)/[^/?#&\s]+",
    re.IGNORECASE,
)

# Hosts that always need an extractor, never a plain GET.
MEDIA_HOSTS = {
    "youtube.com",
    "youtu.be",
    "youtube-nocookie.com",
    "instagram.com",
    "instagr.am",
    "ig.me",
    "tiktok.com",
    "vm.tiktok.com",
    "twitter.com",
    "x.com",
    "vxtwitter.com",
    "fxtwitter.com",
    "facebook.com",
    "fb.watch",
    "vimeo.com",
    "dailymotion.com",
    "dai.ly",
    "reddit.com",
    "redd.it",
    "soundcloud.com",
    "twitch.tv",
    "clips.twitch.tv",
    "pinterest.com",
    "pin.it",
    "snapchat.com",
    "linkedin.com",
    "rumble.com",
    "odysee.com",
    "bilibili.com",
    "vk.com",
    "ok.ru",
    "streamable.com",
    "imgur.com",
}


def is_youtube_url(url: str) -> bool:
    return bool(_YOUTUBE_RE.match(url.strip()))


def is_instagram_url(url: str) -> bool:
    return bool(_INSTAGRAM_RE.match(url.strip()))


def url_host(url: str) -> str:
    try:
        host = (urlparse(url).hostname or "").lower()
    except ValueError:
        return ""
    return host[4:] if host.startswith("www.") else host


def is_media_url(url: str) -> bool:
    host = url_host(url)
    return any(host == candidate or host.endswith("." + candidate) for candidate in MEDIA_HOSTS)


def redact_url(url: str) -> str:
    """Log-friendly URL (host + path only, query strings may hold tokens)."""
    try:
        parsed = urlparse(url)
    except ValueError:
        return "<invalid url>"
    path = parsed.path if len(parsed.path) <= 60 else parsed.path[:57] + "..."
    return f"{parsed.scheme}://{parsed.netloc}{path}"


def _filename_from_disposition(header: str) -> Optional[str]:
    if not header:
        return None
    match = re.search(r"filename\*\s*=\s*(?:UTF-8|utf-8)''([^;]+)", header)
    if match:
        return unquote(match.group(1).strip().strip('"'))
    match = re.search(r'filename\s*=\s*"?([^";]+)"?', header)
    if match:
        return unquote(match.group(1).strip())
    return None


def _guess_title(url: str, filename: str) -> str:
    if filename and filename != "file":
        return filename
    name = Path(urlparse(url).path).name
    return name or "file"


def _extension(filename: str) -> str:
    return Path(filename).suffix.lower()



# --- Direct HTTP ------------------------------------------------------------
def _check_cancel(cancel_event: Optional[asyncio.Event]) -> None:
    if cancel_event is not None and cancel_event.is_set():
        raise DownloadCancelled()


async def download_direct(
    url: str,
    download_id: int,
    progress_callback: Optional[Callable] = None,
    cancel_event: Optional[asyncio.Event] = None,
) -> DownloadResult:
    """Stream a URL to disk with retries, size limits and progress."""
    last_error: Optional[Exception] = None

    for attempt in range(3):
        _check_cancel(cancel_event)
        file_path: Optional[Path] = None
        try:
            session = await get_session()
            timeout = aiohttp.ClientTimeout(
                total=DOWNLOAD_TIMEOUT, sock_connect=30, sock_read=60
            )
            request_kwargs: dict[str, Any] = {
                "allow_redirects": True,
                "timeout": timeout,
                "headers": {"Referer": "https://www.google.com/"},
            }
            if PROXY_URL and not PROXY_URL.lower().startswith(("socks4", "socks5")):
                request_kwargs["proxy"] = PROXY_URL
            async with session.get(url, **request_kwargs) as response:
                if response.status != 200:
                    if response.status in (401, 403):
                        raise DownloadError(
                            "The server refused the download (HTTP "
                            f"{response.status}). The file may be private."
                        )
                    if response.status == 404:
                        raise DownloadError("File not found (HTTP 404).")
                    if response.status == 429:
                        raise DownloadError("The server is rate limiting requests. Try again later.")
                    raise DownloadError(f"Download failed with HTTP {response.status}.")

                content_type = (response.headers.get("Content-Type") or "").lower()
                content_length = response.headers.get("Content-Length")
                total_size = int(content_length) if content_length and content_length.isdigit() else 0

                if total_size and total_size > MAX_BYTES:
                    # Refuse before writing a single byte.
                    raise DownloadError(
                        f"This file is {format_size(total_size)}, but the bot can "
                        f"deliver at most {format_size(MAX_BYTES)}. Pick a smaller "
                        "file or a lower media quality."
                    )

                suggested = _filename_from_disposition(
                    response.headers.get("Content-Disposition", "")
                ) or Path(urlparse(str(response.url)).path).name
                filename = clean_filename(suggested, fallback=f"file_{download_id}")

                if "text/html" in content_type and _extension(filename) not in {".html", ".htm"}:
                    raise _NeedsExtractor(f"content-type {content_type}")

                file_path = DOWNLOADS_DIR / f"{download_id}_{filename}"
                downloaded = 0
                started = time.monotonic()
                last_report = 0.0

                async with aiofiles_open(file_path) as handle:
                    async for chunk in response.content.iter_chunked(CHUNK_SIZE):
                        _check_cancel(cancel_event)
                        if not chunk:
                            continue
                        downloaded += len(chunk)
                        if downloaded > MAX_BYTES:
                            raise DownloadError(
                                f"The file passed {format_size(MAX_BYTES)} (the "
                                "delivery limit), so the download was stopped early."
                            )
                        await handle.write(chunk)

                        if progress_callback:
                            now = time.monotonic()
                            if now - last_report >= _PROGRESS_THROTTLE:
                                last_report = now
                                elapsed = max(now - started, 1e-6)
                                speed = downloaded / elapsed
                                eta = (
                                    (total_size - downloaded) / speed
                                    if speed > 0 and total_size
                                    else 0
                                )
                                progress_callback(downloaded, total_size, speed, eta)

                title = _guess_title(url, filename)
                return DownloadResult(
                    path=str(file_path),
                    size=downloaded,
                    title=title,
                    source="direct",
                )

        except DownloadCancelled:
            _safe_unlink(file_path)
            raise
        except _NeedsExtractor:
            _safe_unlink(file_path)
            raise
        except DownloadError as exc:
            _safe_unlink(file_path)
            raise exc
        except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
            _safe_unlink(file_path)
            last_error = exc
            logger.warning(
                "Attempt %s/3 failed for %s: %s", attempt + 1, redact_url(url), exc
            )
            if attempt < 2:
                await asyncio.sleep(2**attempt)
                continue
        except Exception as exc:  # noqa: BLE001 - normalise everything else
            _safe_unlink(file_path)
            logger.exception("Unexpected download error for %s", redact_url(url))
            raise DownloadError(f"Internal error: {strip_ansi(str(exc))}") from exc

    if isinstance(last_error, asyncio.TimeoutError):
        raise DownloadError("Download timed out. The server was too slow.")
    raise DownloadError("Network error: could not reach the server.")


def aiofiles_open(path: Path):
    import aiofiles

    return aiofiles.open(path, "wb")


def _safe_unlink(path: Optional[Path]) -> None:
    if path is None:
        return
    for candidate in (path, Path(str(path) + ".part")):
        try:
            candidate.unlink(missing_ok=True)
        except OSError as exc:
            logger.debug("Could not remove %s: %s", candidate, exc)


# --- yt-dlp -----------------------------------------------------------------
def _build_ytdlp_options(
    download_id: int,
    options: Optional[dict] = None,
    progress_callback: Optional[Callable] = None,
    cancel_event: Optional[asyncio.Event] = None,
) -> dict:
    ydl_opts: dict[str, Any] = {
        "format": "bv*[ext=mp4]+ba[ext=m4a]/b[ext=mp4]/bv*+ba/b",
        "outtmpl": str(DOWNLOADS_DIR / f"{download_id}_%(title).120B.%(ext)s"),
        "max_filesize": MAX_BYTES,
        "quiet": True,
        "no_warnings": True,
        "noprogress": True,
        "no_color": True,
        "noplaylist": True,
        "ignoreerrors": False,
        "logtostderr": False,
        "cachedir": str(YTDLP_CACHE_DIR),
        "retries": 3,
        "fragment_retries": 3,
        "socket_timeout": 30,
        "concurrent_fragment_downloads": YTDLP_FRAGMENTS,
        "merge_output_format": "mp4",
        "http_headers": {
            "User-Agent": USER_AGENT,
            "Accept-Language": "en-US,en;q=0.9",
        },
    }
    if YTDLP_PROXY:
        ydl_opts["proxy"] = YTDLP_PROXY
    if YTDLP_COOKIES_FILE and os.path.exists(YTDLP_COOKIES_FILE):
        ydl_opts["cookiefile"] = YTDLP_COOKIES_FILE
    if options:
        ydl_opts.update(options)

    if progress_callback or cancel_event:
        last_report = [0.0]

        def hook(data: dict) -> None:
            _check_cancel(cancel_event)
            if data.get("status") != "downloading" or progress_callback is None:
                return
            now = time.monotonic()
            if now - last_report[0] < _PROGRESS_THROTTLE:
                return
            last_report[0] = now
            progress_callback(
                data.get("downloaded_bytes", 0),
                data.get("total_bytes") or data.get("total_bytes_estimate") or 0,
                data.get("speed") or 0,
                data.get("eta") or 0,
            )

        ydl_opts["progress_hooks"] = [hook]

    return ydl_opts


def _find_output(download_id: int, expected: Optional[str] = None) -> Optional[Path]:
    if expected and os.path.exists(expected):
        return Path(expected)
    candidates = [
        path
        for path in DOWNLOADS_DIR.glob(f"{download_id}_*")
        if path.is_file() and path.suffix not in {".part", ".ytdl", ".temp"}
    ]
    if not candidates:
        return None
    return max(candidates, key=lambda path: path.stat().st_mtime)


async def download_with_ytdlp(
    url: str,
    download_id: int,
    options: Optional[dict] = None,
    progress_callback: Optional[Callable] = None,
    cancel_event: Optional[asyncio.Event] = None,
) -> DownloadResult:
    """Extract media with yt-dlp in a worker thread (blocking library)."""
    _check_cancel(cancel_event)
    try:
        import yt_dlp  # imported lazily: keeps startup fast and RAM low
    except ImportError as exc:  # pragma: no cover - dependency is declared
        raise DownloadError(
            "Media extraction is unavailable on this server (yt-dlp missing)."
        ) from exc

    ydl_opts = _build_ytdlp_options(download_id, options, progress_callback, cancel_event)
    loop = asyncio.get_running_loop()

    def _run(opts: dict) -> dict:
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=True)
            if info and info.get("entries"):
                info = info["entries"][0]
            expected = None
            try:
                expected = ydl.prepare_filename(info)
            except Exception:  # pragma: no cover - defensive
                expected = None
            info = dict(info or {})
            info["__expected_path"] = expected
            return info

    async def _extract(opts: dict) -> dict:
        return await loop.run_in_executor(None, _run, opts)

    try:
        try:
            info = await _extract(ydl_opts)
        except Exception as exc:  # noqa: BLE001 - retried below for one case
            # The requested quality may simply not exist for this item.
            if options and "requested format is not available" in strip_ansi(str(exc)).lower():
                logger.info("Requested format unavailable for %s; using the default ladder.", redact_url(url))
                _cleanup_parts(download_id)
                info = await _extract(
                    _build_ytdlp_options(download_id, None, progress_callback, cancel_event)
                )
            else:
                raise
    except DownloadCancelled:
        _cleanup_parts(download_id)
        raise
    except Exception as exc:  # noqa: BLE001 - yt-dlp raises many types
        _cleanup_parts(download_id)
        message = strip_ansi(str(exc))
        logger.error("yt-dlp failed for %s: %s", redact_url(url), message)

        if isinstance(exc, DownloadError):
            raise
        lowered = message.lower()
        if "file is larger than max-filesize" in lowered or "larger than max" in lowered:
            raise DownloadError(
                f"This file is larger than the {format_size(MAX_BYTES)} delivery "
                "limit. Pick a lower quality."
            )
        if "ffmpeg" in lowered and "not found" in lowered:
            raise DownloadError("FFmpeg is missing on the server; cannot process this media.")
        if "not a bot" in lowered or "confirm you" in lowered:
            raise DownloadError(
                "The site is asking for a login (bot check). "
                "Add cookies with YTDLP_COOKIES_FILE, or retry through a proxy."
            )
        if "private" in lowered or "login" in lowered or "sign in" in lowered:
            raise DownloadError("This media is private or requires a login.")
        if "unsupported url" in lowered:
            raise DownloadError("This site is not supported by the extractor.")
        if "timed out" in lowered:
            raise DownloadError("The site took too long to respond. Try again.")
        raise DownloadError(f"Could not process this link. {message[:220]}")

    expected = info.get("__expected_path")
    path = _find_output(download_id, expected)
    if path is None:
        raise DownloadError("The download finished but no file was produced.")

    if path.stat().st_size > MAX_BYTES:
        _cleanup_parts(download_id)
        _safe_unlink(path)
        raise DownloadError(
            f"This file is larger than the {format_size(MAX_BYTES)} delivery "
            "limit. Pick a lower quality."
        )
    # yt-dlp keeps the separated streams on disk until it has merged them; make
    # sure nothing but the finished file survives.
    _cleanup_parts(download_id, keep=path)

    width = info.get("width")
    height = info.get("height")
    duration = info.get("duration")
    if not width or not height:
        for item in (info.get("requested_downloads") or []):
            width = width or item.get("width")
            height = height or item.get("height")

    return DownloadResult(
        path=str(path),
        size=path.stat().st_size,
        title=(info.get("title") or path.stem)[:200],
        duration=int(duration) if duration else None,
        width=int(width) if width else None,
        height=int(height) if height else None,
        artist=(info.get("uploader") or info.get("artist") or None),
        source="yt-dlp",
    )


def _cleanup_parts(download_id: int, keep: Optional[Path] = None) -> None:
    """Delete partials, fragments and stray siblings for one download id."""
    for path in DOWNLOADS_DIR.glob(f"{download_id}_*"):
        if keep is not None and path == keep:
            continue
        if path.suffix in {".part", ".ytdl", ".temp", ".frag", ".ytdlpart"}:
            _safe_unlink(path)
        elif keep is not None and path.is_file() and path != keep:
            # left-over audio/video stream after a merge
            _safe_unlink(path)


# --- Public API -------------------------------------------------------------
async def download_file(
    url: str,
    download_id: int,
    ytdlp_options: Optional[dict] = None,
    progress_callback: Optional[Callable] = None,
    cancel_event: Optional[asyncio.Event] = None,
) -> DownloadResult:
    """Download ``url``. Media pages fall back to yt-dlp automatically."""
    if is_media_url(url) or ytdlp_options:
        return await download_with_ytdlp(
            url, download_id, ytdlp_options, progress_callback, cancel_event
        )

    try:
        return await download_direct(url, download_id, progress_callback, cancel_event)
    except _NeedsExtractor as exc:
        logger.info("Falling back to yt-dlp for %s (%s)", redact_url(url), exc)
        return await download_with_ytdlp(
            url, download_id, ytdlp_options, progress_callback, cancel_event
        )


def cleanup_file(file_path: Optional[str]) -> None:
    if not file_path:
        return
    try:
        Path(file_path).unlink(missing_ok=True)
        logger.info("Removed temporary file %s", Path(file_path).name)
    except OSError as exc:
        logger.error("Error cleaning up %s: %s", file_path, exc)
