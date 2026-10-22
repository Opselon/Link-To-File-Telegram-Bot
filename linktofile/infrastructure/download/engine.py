"""Download engine: direct HTTP streaming plus yt-dlp for media pages."""

from __future__ import annotations

import asyncio
import logging
import os
import re
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Optional
from urllib.parse import unquote, urljoin, urlparse

import aiohttp

from linktofile.config import (
    DOWNLOAD_TIMEOUT,
    DOWNLOADS_DIR,
    HTTP_POOL_SIZE,
    INSTANT_HOSTS,
    INSTANT_PROXY,
    SSRF_MAX_REDIRECTS,
    STALL_TIMEOUT,
    UPLOAD_LIMIT_BYTES,
    PROXY_URL,
    YTDLP_CACHE_DIR,
    YTDLP_COOKIES_FILE,
    YTDLP_FRAGMENTS,
    YTDLP_PROXY,
    YTDLP_THREADS,
)
from linktofile.domain.errors import (
    DownloadCancelled as CancelledError,
    DownloadFailed,
    LoginRequired,
    MediaUnavailable,
    StalledDownload,
    TransientDownloadFailed,
    UnsafeUrl,
)
from linktofile.shared.security import ensure_public_url
from linktofile.shared.telemetry import Timer, pipeline
from linktofile.shared.utils import clean_filename, format_size, strip_ansi

logger = logging.getLogger(__name__)

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)

MAX_BYTES = UPLOAD_LIMIT_BYTES
_PROGRESS_THROTTLE = 0.4


class DownloadError(DownloadFailed):
    """A user-presentable download failure.

    Subclassing ``DownloadFailed`` (a ``BotError``) is what lets the workflow
    render the reason to the user: a plain ``Exception`` here used to fall
    through to "Something broke on my side", which hid every real cause.
    """


class DownloadCancelled(CancelledError, DownloadError):
    """The user stopped this download (recognised as a cancel, not a failure)."""


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
    shutdown_executor()


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


def is_media_site(url: str) -> bool:
    """True only for a known *site* (not a URL that merely ends in .mp4).

    Direct file links should go straight to the downloader — probing them with
    an extractor would just burn the preview timeout.
    """
    return is_media_url(url)


def is_instant_url(url: str) -> bool:
    """True for sites that should skip the quality picker entirely.

    Instagram (and anything else in ``INSTANT_HOSTS``) serves one media file per
    post, so asking "which quality?" only adds a round-trip and a preview probe
    before the same download happens. Those links go straight to the queue.
    """
    host = url_host(url)
    if not host:
        return False
    return any(host == candidate or host.endswith("." + candidate) for candidate in INSTANT_HOSTS)


def proxy_for(url: str) -> Optional[str]:
    """Egress for ``url``: instant hosts go direct unless INSTANT_PROXY is set.

    The shared YTDLP_PROXY exists for sites that block this server (YouTube's
    bot check). Routing Instagram through it costs seconds and some exits are
    refused outright, so those hosts use their own setting.
    """
    if is_instant_url(url):
        return INSTANT_PROXY
    return YTDLP_PROXY


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


_REDIRECT_STATUSES = {301, 302, 303, 307, 308}


def _classify_http_status(status: int) -> DownloadFailed:
    """Map a non-200 answer onto the right error class.

    Only retryable answers become ``TransientDownloadFailed``; a private file or
    a 404 must fail immediately instead of burning the retry budget.
    """
    if status in (401, 403):
        return DownloadError(
            f"The server refused the download (HTTP {status}). The file may be private."
        )
    if status == 404:
        return DownloadError("File not found (HTTP 404).")
    if status == 429:
        return TransientDownloadFailed(
            "The server is rate limiting requests. Retrying shortly."
        )
    if status in (408, 425) or 500 <= status < 600:
        return TransientDownloadFailed(f"The server answered HTTP {status}. Retrying.")
    return DownloadError(f"Download failed with HTTP {status}.")


async def _stream_to_disk(
    response: aiohttp.ClientResponse,
    url: str,
    download_id: int,
    cancel_event: Optional[asyncio.Event],
    progress_callback: Optional[Callable],
    *,
    stall_timeout: float,
) -> DownloadResult:
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

    try:
        async with aiofiles_open(file_path) as handle:
            while True:
                _check_cancel(cancel_event)
                try:
                    chunk = await asyncio.wait_for(
                        response.content.readany(), timeout=stall_timeout
                    )
                except asyncio.TimeoutError as exc:
                    pipeline(
                        "download.stalled",
                        ticket=download_id,
                        host=url_host(url),
                        stall_s=stall_timeout,
                        bytes=downloaded,
                        level=logging.WARNING,
                    )
                    raise StalledDownload(
                        f"Nothing arrived for {int(stall_timeout)} seconds — the server stalled."
                    ) from exc
                if not chunk:
                    break
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
    except BaseException:
        # A half-written file must never look like a finished download, even
        # when the task is cancelled from the outside.
        _safe_unlink(file_path)
        raise

    title = _guess_title(url, filename)
    pipeline(
        "download.done",
        ticket=download_id,
        host=url_host(url),
        source="direct",
        bytes=downloaded,
        ms=int((time.monotonic() - started) * 1000),
    )
    return DownloadResult(
        path=str(file_path),
        size=downloaded,
        title=title,
        source="direct",
    )


async def download_direct(
    url: str,
    download_id: int,
    progress_callback: Optional[Callable] = None,
    cancel_event: Optional[asyncio.Event] = None,
    *,
    stall_timeout: Optional[float] = None,
) -> DownloadResult:
    """Stream a URL to disk once, with SSRF-validated redirects and a stall guard.

    Retries live in the workflow (``DownloadWorkflow``): the engine reports a
    ``TransientDownloadFailed`` and stops, so the attempt count, the backoff and
    the log line exist in exactly one place instead of two nested loops.
    """
    _check_cancel(cancel_event)
    stall = float(stall_timeout if stall_timeout is not None else STALL_TIMEOUT)
    file_path: Optional[Path] = None
    current = url

    try:
        session = await get_session()
        timeout = aiohttp.ClientTimeout(total=DOWNLOAD_TIMEOUT, sock_connect=30, sock_read=60)

        for hop in range(SSRF_MAX_REDIRECTS + 1):
            # Validated before every hop: a public URL must not be able to 302
            # onto the loopback interface or the cloud metadata endpoint.
            await ensure_public_url(current)

            request_kwargs: dict[str, Any] = {
                "allow_redirects": False,
                "timeout": timeout,
                "headers": {"Referer": "https://www.google.com/"},
            }
            if PROXY_URL and not PROXY_URL.lower().startswith(("socks4", "socks5")):
                request_kwargs["proxy"] = PROXY_URL

            async with session.get(current, **request_kwargs) as response:
                if response.status in _REDIRECT_STATUSES:
                    location = response.headers.get("Location")
                    if not location:
                        raise DownloadError("The server sent a redirect without a target.")
                    current = urljoin(str(response.url), location)
                    pipeline(
                        "download.redirect",
                        ticket=download_id,
                        host=url_host(url),
                        hop=hop + 1,
                        to=url_host(current),
                    )
                    continue

                if response.status != 200:
                    raise _classify_http_status(response.status)

                return await _stream_to_disk(
                    response,
                    url,
                    download_id,
                    cancel_event,
                    progress_callback,
                    stall_timeout=stall,
                )

        raise DownloadError("The link redirected too many times.")

    except DownloadCancelled:
        _safe_unlink(file_path)
        raise
    except _NeedsExtractor:
        _safe_unlink(file_path)
        raise
    except (UnsafeUrl, DownloadFailed):
        _safe_unlink(file_path)
        raise
    except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
        _safe_unlink(file_path)
        logger.warning("Transient failure for %s: %s", redact_url(url), exc)
        raise TransientDownloadFailed(
            "Network error: the server dropped the connection. Retrying."
        ) from exc
    except Exception as exc:  # noqa: BLE001 - normalise everything else
        _safe_unlink(file_path)
        logger.exception("Unexpected download error for %s", redact_url(url))
        raise DownloadError(f"Internal error: {strip_ansi(str(exc))}") from exc


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
_executor: Optional[ThreadPoolExecutor] = None


def _get_executor() -> ThreadPoolExecutor:
    """One bounded pool for extractor work.

    The default executor grows with the machine and is shared with every other
    blocking call; a burst of links could spawn dozens of yt-dlp threads. This
    pool is sized to the worker count, so memory use stays predictable.
    """
    global _executor
    if _executor is None:
        _executor = ThreadPoolExecutor(
            max_workers=YTDLP_THREADS, thread_name_prefix="ytdlp"
        )
        logger.debug("Created yt-dlp executor with %s thread(s)", YTDLP_THREADS)
    return _executor


def shutdown_executor() -> None:
    global _executor
    if _executor is not None:
        _executor.shutdown(wait=False, cancel_futures=True)
        _executor = None


async def _await_with_stall_watchdog(
    future: "asyncio.Future",
    heartbeat: dict,
    *,
    ticket_id: int,
    host: str,
    stall_timeout: float,
) -> dict:
    """Wait for an executor future, failing fast when it stops making progress.

    yt-dlp runs in a thread that cannot be cancelled; abandoning the await is
    still the right move: the job is marked failed, its card is updated, and the
    janitor reaps whatever the thread eventually leaves behind.
    """
    poll = max(1.0, min(15.0, stall_timeout / 4))
    while True:
        done, _ = await asyncio.wait({future}, timeout=poll)
        if done:
            return future.result()
        idle = time.monotonic() - float(heartbeat.get("last", 0.0))
        if idle >= stall_timeout:
            future.cancel()
            pipeline(
                "extract.stalled",
                ticket=ticket_id,
                host=host,
                idle_s=int(idle),
                level=logging.WARNING,
            )
            raise StalledDownload(
                f"The site stopped sending data for {int(idle)}s — retrying."
            )


_MAX_FILESIZE_HINTS = ("file is larger than max-filesize", "larger than max")
_LOGIN_HINTS = ("login_required", "you've been logged out", "empty media response")
_PRIVATE_HINTS = ("private", "sign in", "sign-in")
_BOT_HINTS = ("not a bot", "confirm you", "confirm you're", "checkpoint")
# Phrases that mean "this media is gone", not "log in": telling a user to add
# cookies for a deleted reel is wrong, so these are checked first.
_UNAVAILABLE_HINTS = (
    "video unavailable",
    "media is unavailable",
    "this video is not available",
    "no longer available",
    "removed by the uploader",
    "has been removed",
    "no video formats found",
    "requested format is not available",
    "content is not available",
    "does not exist",
)
# Network-level failures worth a retry; anything else is the site's answer.
_TRANSIENT_HINTS = (
    "connection reset",
    "connection aborted",
    "connection refused",
    "temporary failure in name resolution",
    "read timed out",
    "timed out",
    "http error 408",
    "http error 429",
    "http error 500",
    "http error 502",
    "http error 503",
    "http error 504",
    "unable to download webpage",
    "incompleteread",
)


def classify_ytdlp_failure(message: str, *, host: str = "") -> DownloadFailed:
    """Turn one yt-dlp error string into the most precise user-facing error.

    ``MediaUnavailable`` beats ``LoginRequired`` when both apply: Instagram
    answers private/deleted posts with the same empty response a login wall
    gives, and telling a user to "add cookies" for a gone reel is wrong.
    """
    text = strip_ansi(str(message))
    lowered = text.lower()
    instagram = host.endswith(("instagram.com", "instagr.am", "ig.me", "cdninstagram.com"))

    if any(hint in lowered for hint in _MAX_FILESIZE_HINTS):
        return DownloadError(
            f"This file is larger than the {format_size(MAX_BYTES)} delivery "
            "limit. Pick a lower quality."
        )
    if "ffmpeg" in lowered and "not found" in lowered:
        return DownloadError("FFmpeg is missing on the server; cannot process this media.")
    if any(hint in lowered for hint in _UNAVAILABLE_HINTS):
        return MediaUnavailable(
            "This media is unavailable — it was removed, made private, or is "
            "restricted in this region."
        )
    if any(hint in lowered for hint in _LOGIN_HINTS):
        if instagram:
            return MediaUnavailable(
                "Instagram didn't hand back this media. Reels download only while "
                "they're public — private, deleted or age-restricted posts return nothing."
            )
        return LoginRequired(
            "This link needs a logged-in session. Only public pages can be fetched."
        )
    if "login required" in lowered or "rate limit" in lowered or "rate-limit" in lowered:
        return LoginRequired("This media needs a login, or the site is rate limiting me.")
    if any(hint in lowered for hint in _BOT_HINTS):
        return LoginRequired(
            "The site is asking for a login (bot check). "
            "Add cookies with YTDLP_COOKIES_FILE, or retry through a proxy."
        )
    if any(hint in lowered for hint in _PRIVATE_HINTS) or "login" in lowered:
        return LoginRequired("This media is private or requires a login.")
    if "unsupported url" in lowered:
        return DownloadError("This site is not supported by the extractor.")
    if any(hint in lowered for hint in _TRANSIENT_HINTS):
        return TransientDownloadFailed(
            "The site dropped the connection. Retrying."
        )
    return DownloadError(f"Could not process this link. {text[:220]}")


def _build_ytdlp_options(
    download_id: int,
    options: Optional[dict] = None,
    progress_callback: Optional[Callable] = None,
    cancel_event: Optional[asyncio.Event] = None,
    *,
    url: str = "",
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
    proxy = proxy_for(url)
    if proxy:
        ydl_opts["proxy"] = proxy
    if YTDLP_COOKIES_FILE and os.path.exists(YTDLP_COOKIES_FILE):
        ydl_opts["cookiefile"] = YTDLP_COOKIES_FILE
    if options:
        ydl_opts.update(options)

    if progress_callback or cancel_event:
        last_report = [0.0]
        warned = [False]

        def hook(data: dict) -> None:
            _check_cancel(cancel_event)
            if data.get("status") != "downloading" or progress_callback is None:
                return
            now = time.monotonic()
            if now - last_report[0] < _PROGRESS_THROTTLE:
                return
            last_report[0] = now
            try:
                progress_callback(
                    data.get("downloaded_bytes", 0),
                    data.get("total_bytes") or data.get("total_bytes_estimate") or 0,
                    data.get("speed") or 0,
                    data.get("eta") or 0,
                )
            except Exception as exc:  # noqa: BLE001 - a UI refresh must never fail a download
                # Hooks run inside yt-dlp's download loop: letting a card update
                # raise here aborts the transfer and reports a download failure.
                if not warned[0]:
                    warned[0] = True
                    logger.warning("Progress callback failed (ignored): %s", exc)
                return

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


def _wrap_progress_heartbeat(
    progress_callback: Optional[Callable],
    heartbeat: dict,
) -> Callable:
    """Progress calls also refresh the stall watchdog's clock.

    Always returns a callback: the hook is what keeps yt-dlp's *own* download
    progress visible to the watchdog even when the caller asked for no card.
    """

    def wrapped(downloaded: int, total: int, speed: float, eta: float) -> None:
        heartbeat["last"] = time.monotonic()
        if progress_callback is not None:
            progress_callback(downloaded, total, speed, eta)

    return wrapped


async def download_with_ytdlp(
    url: str,
    download_id: int,
    options: Optional[dict] = None,
    progress_callback: Optional[Callable] = None,
    cancel_event: Optional[asyncio.Event] = None,
    *,
    stall_timeout: Optional[float] = None,
) -> DownloadResult:
    """Extract media with yt-dlp in a bounded worker thread (blocking library)."""
    _check_cancel(cancel_event)
    try:
        import yt_dlp  # imported lazily: keeps startup fast and RAM low
    except ImportError as exc:  # pragma: no cover - dependency is declared
        raise DownloadError(
            "Media extraction is unavailable on this server (yt-dlp missing)."
        ) from exc

    stall = float(stall_timeout if stall_timeout is not None else STALL_TIMEOUT)
    heartbeat = {"last": time.monotonic()}
    ydl_opts = _build_ytdlp_options(
        download_id,
        options,
        _wrap_progress_heartbeat(progress_callback, heartbeat),
        cancel_event,
        url=url,
    )
    loop = asyncio.get_running_loop()
    host = url_host(url)
    timer = Timer()
    pipeline(
        "extract.start",
        ticket=download_id,
        host=host,
        format=str(ydl_opts.get("format") or "")[:60],
        cookies=bool(ydl_opts.get("cookiefile")),
        proxy=bool(ydl_opts.get("proxy")),
    )

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
        future = loop.run_in_executor(_get_executor(), _run, opts)
        return await _await_with_stall_watchdog(
            future,
            heartbeat,
            ticket_id=download_id,
            host=host,
            stall_timeout=stall,
        )

    try:
        try:
            info = await _extract(ydl_opts)
        except Exception as exc:  # noqa: BLE001 - retried below for one case
            # The requested quality may simply not exist for this item.
            if options and "requested format is not available" in strip_ansi(str(exc)).lower():
                logger.info("Requested format unavailable for %s; using the default ladder.", redact_url(url))
                pipeline("extract.retry", ticket=download_id, host=host, reason="format_unavailable")
                _cleanup_parts(download_id)
                info = await _extract(
                    _build_ytdlp_options(
                        download_id, None, progress_callback, cancel_event, url=url
                    )
                )
            else:
                raise
    except DownloadCancelled:
        _cleanup_parts(download_id)
        pipeline("extract.cancelled", ticket=download_id, host=host, ms=timer.elapsed)
        raise
    except Exception as exc:  # noqa: BLE001 - yt-dlp raises many types
        _cleanup_parts(download_id)
        message = strip_ansi(str(exc))
        if isinstance(exc, DownloadError):
            raise
        error = classify_ytdlp_failure(message, host=host)
        logger.warning("yt-dlp failed for %s: %s", redact_url(url), message)
        pipeline(
            "extract.failed",
            ticket=download_id,
            host=host,
            ms=timer.elapsed,
            category=type(error).__name__,
            reason=str(error)[:180],
            detail=message[:180],
            level=logging.WARNING,
        )
        raise error

    pipeline(
        "extract.done",
        ticket=download_id,
        host=host,
        ms=timer.elapsed,
        formats=len(info.get("formats") or []),
        extractor=str(info.get("extractor_key") or info.get("extractor") or ""),
    )
    expected = info.get("__expected_path")
    path = _find_output(download_id, expected)
    if path is None:
        pipeline("file.missing", ticket=download_id, host=host, level=logging.ERROR)
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

    size = path.stat().st_size
    pipeline(
        "download.done",
        ticket=download_id,
        host=host,
        source="yt-dlp",
        bytes=size,
        ms=timer.elapsed,
        resolution=f"{width}x{height}" if width and height else "",
    )
    return DownloadResult(
        path=str(path),
        size=size,
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
    pipeline(
        "download.dispatch",
        ticket=download_id,
        host=url_host(url),
        lane="extractor" if (is_media_url(url) or ytdlp_options) else "direct",
    )
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
