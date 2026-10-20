"""Small, dependency-free helpers: URL validation, formatting, rate limits."""

from __future__ import annotations

import ipaddress
import math
import re
import time
from typing import Dict
from urllib.parse import urlparse

from config import RATE_LIMIT_SECONDS

# ANSI escape codes (yt-dlp likes to colour its errors).
ANSI_ESCAPE = re.compile(r"\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])")
_CONTROL_CHARS = re.compile(r"[\x00-\x1f\x7f]")

# Simple in-memory rate limiting: {user_id: last_request_time}
user_last_request: Dict[int, float] = {}
_MAX_RATE_LIMIT_ENTRIES = 50_000

_ALLOWED_SCHEMES = {"http", "https"}
_BLOCKED_HOSTNAMES = {
    "localhost",
    "localhost.localdomain",
    "ip6-localhost",
    "metadata.google.internal",
}


def is_valid_url(url: str) -> bool:
    """True for a plausible absolute http(s) URL."""
    if not url or len(url) > 4096:
        return False
    if any(char.isspace() for char in url):
        return False
    try:
        result = urlparse(url)
    except ValueError:
        return False
    if result.scheme.lower() not in _ALLOWED_SCHEMES:
        return False
    if not result.netloc:
        return False
    return bool(result.hostname)


def is_safe_url(url: str) -> bool:
    """Reject loopback/private/link-local targets (SSRF guard)."""
    try:
        parsed_url = urlparse(url)
        hostname = parsed_url.hostname
        if not hostname:
            return False

        hostname = hostname.strip("[]").lower()
        if hostname in _BLOCKED_HOSTNAMES or hostname.endswith(".local"):
            return False

        try:
            ip = ipaddress.ip_address(hostname)
        except ValueError:
            # Not an IP literal; a DNS name is acceptable here. The HTTP client
            # still resolves it, so obviously-local names are blocked above.
            return True
        return not (
            ip.is_private
            or ip.is_loopback
            or ip.is_link_local
            or ip.is_multicast
            or ip.is_reserved
            or ip.is_unspecified
        )
    except Exception:
        return False


def rate_limit_remaining(user_id: int) -> int:
    """Seconds the user still has to wait (0 when they may proceed)."""
    last = user_last_request.get(user_id)
    if not last:
        return 0
    remaining = RATE_LIMIT_SECONDS - (time.time() - last)
    return max(0, int(math.ceil(remaining)))


def check_rate_limit(user_id: int) -> bool:
    """Record and allow the request, or reject it while the window is open."""
    if rate_limit_remaining(user_id) > 0:
        return False
    if len(user_last_request) > _MAX_RATE_LIMIT_ENTRIES:
        cutoff = time.time() - max(RATE_LIMIT_SECONDS, 60)
        for key in [k for k, ts in user_last_request.items() if ts < cutoff]:
            user_last_request.pop(key, None)
        if len(user_last_request) > _MAX_RATE_LIMIT_ENTRIES:
            user_last_request.clear()
    user_last_request[user_id] = time.time()
    return True


def format_size(size_bytes: float | int | None) -> str:
    if not size_bytes or size_bytes < 0:
        return "0 B"
    size_name = ("B", "KB", "MB", "GB", "TB")
    index = min(int(math.floor(math.log(size_bytes, 1024))), len(size_name) - 1)
    value = size_bytes / (1024**index)
    precision = 0 if index == 0 or value >= 100 else 1
    return f"{value:.{precision}f} {size_name[index]}"


def format_eta(seconds: float | int | None) -> str:
    if not seconds or seconds <= 0 or seconds > 86_400:
        return "—"
    seconds = int(seconds)
    hours, remainder = divmod(seconds, 3600)
    minutes, secs = divmod(remainder, 60)
    if hours:
        return f"{hours}h {minutes:02d}m"
    if minutes:
        return f"{minutes}m {secs:02d}s"
    return f"{secs}s"


def format_duration(seconds: float | int | None) -> str:
    if not seconds or seconds <= 0:
        return ""
    seconds = int(seconds)
    hours, remainder = divmod(seconds, 3600)
    minutes, secs = divmod(remainder, 60)
    if hours:
        return f"{hours}:{minutes:02d}:{secs:02d}"
    return f"{minutes}:{secs:02d}"


def format_speed(speed: float | int | None) -> str:
    if not speed or speed <= 0 or math.isinf(speed) or math.isnan(speed):
        return "—"
    return f"{format_size(int(speed))}/s"


def strip_ansi(text: str) -> str:
    """Remove ANSI escape codes from a string."""
    return ANSI_ESCAPE.sub("", text or "")


def clean_filename(name: str, *, fallback: str = "file", max_length: int = 120) -> str:
    """Make an arbitrary remote name safe for the local filesystem."""
    name = strip_ansi(name or "").replace("\\", "/").split("/")[-1]
    name = _CONTROL_CHARS.sub("", name).strip().strip(".")
    name = re.sub(r"\s+", " ", name)
    if not name or name in {".", ".."}:
        return fallback
    if len(name) > max_length:
        stem, dot, suffix = name.rpartition(".")
        if dot and len(suffix) <= 8:
            name = stem[: max_length - len(suffix) - 1] + "." + suffix
        else:
            name = name[:max_length]
    return name


def _number(value, default: float = 0.0) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    if math.isnan(number) or math.isinf(number):
        return default
    return number


def get_progress_bar(
    downloaded: int,
    total: int,
    speed: float,
    eta: float,
    *,
    stage: str = "Downloading",
    width: int = 12,
) -> str:
    """Render the progress card body (HTML)."""
    downloaded = max(0, int(_number(downloaded)))
    total = max(0, int(_number(total)))
    speed = max(0.0, _number(speed))
    eta = max(0.0, _number(eta))

    if total > 0:
        ratio = min(1.0, downloaded / total)
        filled = min(width, max(0, int(round(ratio * width))))
        percent = f"{ratio * 100:.1f}%"
        bar = "█" * filled + "░" * (width - filled)
        total_label = format_size(total)
    else:
        percent = "…"
        bar = "░" * width
        total_label = "?"

    return (
        f"<b>{stage}</b>\n"
        f"<code>{bar}</code> {percent}\n\n"
        f"<b>{format_size(downloaded)}</b> of <b>{total_label}</b>\n"
        f"⚡ {format_speed(speed)}   ⏳ {format_eta(eta)}"
    )
