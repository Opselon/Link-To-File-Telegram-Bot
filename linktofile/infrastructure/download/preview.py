"""yt-dlp metadata probe for the preview card.

``extract_info(download=False)`` costs one page fetch and gives the title,
uploader, duration and thumbnail. It runs in a worker thread because yt-dlp is a
blocking library, and honours the same proxy/cookie settings as a real download
so a site that needs them still previews.
"""

from __future__ import annotations

import asyncio
import logging
import os
from typing import Any, Optional

from linktofile.config import YTDLP_COOKIES_FILE, YTDLP_PROXY
from linktofile.domain.models import MediaInfo
from linktofile.infrastructure.download.engine import USER_AGENT, url_host

logger = logging.getLogger(__name__)


class _QuietLogger:
    """Route yt-dlp's chatter to our debug log instead of stderr.

    Without this, a failed preview prints a scary "ERROR:" line into the
    container log for every unsupported link.
    """

    def debug(self, message: str) -> None:
        logger.debug("yt-dlp: %s", message)

    def info(self, message: str) -> None:  # pragma: no cover - informational only
        logger.debug("yt-dlp: %s", message)

    def warning(self, message: str) -> None:
        logger.debug("yt-dlp: %s", message)

    def error(self, message: str) -> None:
        logger.debug("yt-dlp: %s", message)


class YtDlpProbe:
    """Implements the ``MediaProbe`` port with yt-dlp."""

    async def probe(self, url: str) -> Optional[MediaInfo]:
        try:
            import yt_dlp  # imported lazily: startup stays fast
        except ImportError:  # pragma: no cover - dependency is declared
            return None

        loop = asyncio.get_running_loop()
        try:
            info = await loop.run_in_executor(None, self._peek, yt_dlp, url)
        except Exception as exc:  # noqa: BLE001 - a failed preview is not fatal
            logger.debug("Probe failed for %s: %s", url_host(url) or "url", exc)
            return None
        return self._to_media_info(url, info)

    # --- internals ----------------------------------------------------------
    def _peek(self, yt_dlp, url: str) -> Any:
        options: dict[str, Any] = {
            "quiet": True,
            "no_warnings": True,
            "no_color": True,
            "noplaylist": True,
            "skip_download": True,
            "logtostderr": False,
            "socket_timeout": 15,
            "retries": 1,
            "ignoreerrors": False,
            "logger": _QuietLogger(),
            "http_headers": {
                "User-Agent": USER_AGENT,
                "Accept-Language": "en-US,en;q=0.9",
            },
        }
        if YTDLP_PROXY:
            options["proxy"] = YTDLP_PROXY
        if YTDLP_COOKIES_FILE and os.path.exists(YTDLP_COOKIES_FILE):
            options["cookiefile"] = YTDLP_COOKIES_FILE
        with yt_dlp.YoutubeDL(options) as ydl:
            return ydl.extract_info(url, download=False)

    @staticmethod
    def _to_media_info(url: str, info: Any) -> Optional[MediaInfo]:
        if not isinstance(info, dict):
            return None
        entries = info.get("entries")
        if entries:
            first = entries[0] if isinstance(entries, list) else None
            info = first if isinstance(first, dict) else {}

        title = str(info.get("title") or info.get("id") or "").strip()
        if not title:
            return None

        duration = info.get("duration")
        views = info.get("view_count")
        uploader = info.get("uploader") or info.get("channel") or info.get("uploader_id")
        thumbnail = info.get("thumbnail")

        return MediaInfo(
            title=title[:200],
            uploader=str(uploader)[:120] if uploader else None,
            duration=int(duration) if duration else None,
            thumbnail=str(thumbnail) if thumbnail else None,
            site=url_host(url),
            url=str(info.get("webpage_url") or url),
            view_count=int(views) if views else None,
        )


__all__ = ["YtDlpProbe"]
