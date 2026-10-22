"""Central configuration.

Every value can be set through the environment or a local ``.env`` file, so the
same code runs bare-metal, in Docker and in CI without edits.
"""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent

# Real environment variables win over the .env file (important in Docker, where
# `env_file`/`environment` is the source of truth).
load_dotenv(BASE_DIR / ".env", override=False)


def _text(name: str, default: str = "") -> str:
    value = os.getenv(name)
    return default if value is None else value.strip()


def _int(
    name: str,
    default: int,
    *,
    minimum: int | None = None,
    maximum: int | None = None,
) -> int:
    raw = os.getenv(name)
    if raw is None or str(raw).strip() == "":
        value = default
    else:
        try:
            value = int(float(str(raw).strip()))
        except (TypeError, ValueError):
            value = default
    if minimum is not None:
        value = max(minimum, value)
    if maximum is not None:
        value = min(maximum, value)
    return value


# --- Bot identity -----------------------------------------------------------
BOT_TOKEN = _text("BOT_TOKEN")
# Optional self-hosted Bot API server (needed for >50 MB uploads).
TELEGRAM_API_URL = _text("TELEGRAM_API_URL") or None
ADMIN_ID = _int("ADMIN_ID", 0) or None

# --- Limits -----------------------------------------------------------------
# Telegram Bot API caps uploads at 2000 MB.
MAX_FILE_SIZE_MB = _int("MAX_FILE_SIZE_MB", 2000, minimum=1, maximum=2000)

# What the configured Bot API can actually deliver. Anything bigger is wasted
# bandwidth and disk, so downloads are capped here, not at MAX_FILE_SIZE_MB.
UPLOAD_LIMIT_MB = MAX_FILE_SIZE_MB if TELEGRAM_API_URL else min(MAX_FILE_SIZE_MB, 50)
UPLOAD_LIMIT_BYTES = UPLOAD_LIMIT_MB * 1024 * 1024

# Housekeeping: refuse work on a nearly full disk and sweep leftovers.
MIN_FREE_DISK_MB = _int("MIN_FREE_DISK_MB", 512, minimum=0)
TEMP_FILE_TTL = _int("TEMP_FILE_TTL", 900, minimum=60)
JANITOR_INTERVAL = _int("JANITOR_INTERVAL", 300, minimum=60)
MAX_PENDING_PER_USER = _int("MAX_PENDING_PER_USER", 2, minimum=1)
DOWNLOAD_TIMEOUT = _int("DOWNLOAD_TIMEOUT", 600, minimum=10, maximum=86_400)
QUEUE_TASK_TIMEOUT = _int("QUEUE_TASK_TIMEOUT", 1800, minimum=60, maximum=86_400)
QUEUE_CONCURRENCY = _int("QUEUE_CONCURRENCY", 3, minimum=1, maximum=16)
QUEUE_MAX_SIZE = _int("QUEUE_MAX_SIZE", 200, minimum=1, maximum=10_000)
RATE_LIMIT_SECONDS = _int("RATE_LIMIT_SECONDS", 3, minimum=0, maximum=3600)

# --- Resilience -------------------------------------------------------------
# How many times one job may hit a *transient* failure before the user is told
# it failed. Backoff doubles from RETRY_BACKOFF_SECONDS, capped at the max.
DOWNLOAD_RETRIES = _int("DOWNLOAD_RETRIES", 3, minimum=1, maximum=10)
RETRY_BACKOFF_SECONDS = _int("RETRY_BACKOFF_SECONDS", 2, minimum=0, maximum=120)
RETRY_BACKOFF_MAX = _int("RETRY_BACKOFF_MAX", 30, minimum=0, maximum=600)
# A transfer that delivers nothing at all for this long is treated as stalled
# and retried instead of hanging until the whole task times out.
STALL_TIMEOUT = _int("STALL_TIMEOUT", 120, minimum=5, maximum=3600)
# Telegram flood waits are honoured up to this long, then the upload fails
# cleanly instead of blocking a worker forever.
FLOOD_WAIT_MAX = _int("FLOOD_WAIT_MAX", 60, minimum=1, maximum=900)

# --- Security ---------------------------------------------------------------
# Fail-closed URL policy: public http(s) only, no loopback/private/metadata.
# SSRF_DNS_CHECK=0 keeps the static checks but skips resolution (tests, or an
# operator behind a split-horizon DNS they trust).
SSRF_ENFORCE = _int("SSRF_ENFORCE", 1, minimum=0, maximum=1) == 1
SSRF_DNS_CHECK = _int("SSRF_DNS_CHECK", 1, minimum=0, maximum=1) == 1
SSRF_MAX_REDIRECTS = _int("SSRF_MAX_REDIRECTS", 5, minimum=0, maximum=20)
# Total bytes of temp files allowed before new jobs are refused.
MAX_TEMP_MB = _int("MAX_TEMP_MB", 4096, minimum=0)
# yt-dlp runs in a bounded thread pool: unbounded executors are how a burst of
# links turns into an OOM.
YTDLP_THREADS = _int("YTDLP_THREADS", max(2, QUEUE_CONCURRENCY), minimum=1, maximum=32)

# Resume jobs committed to the durable queue by the previous process.
RESUME_ON_STARTUP = _int("RESUME_ON_STARTUP", 1, minimum=0, maximum=1) == 1
RESUME_MAX_JOBS = _int("RESUME_MAX_JOBS", 50, minimum=0, maximum=1000)

# --- Performance ------------------------------------------------------------
DOWNLOAD_CHUNK_KB = _int("DOWNLOAD_CHUNK_KB", 256, minimum=16, maximum=8192)
UPLOAD_CHUNK_KB = _int("UPLOAD_CHUNK_KB", 1024, minimum=64, maximum=8192)
PROGRESS_INTERVAL = float(_int("PROGRESS_INTERVAL_MS", 1500, minimum=300, maximum=30_000)) / 1000
YTDLP_FRAGMENTS = _int("YTDLP_FRAGMENTS", 8, minimum=1, maximum=32)
HTTP_POOL_SIZE = _int("HTTP_POOL_SIZE", 64, minimum=4, maximum=256)

# How long a media page may take to describe itself before the bot shows the
# plain quality picker instead. 0 disables the preview card entirely.
PREVIEW_TIMEOUT = _int("PREVIEW_TIMEOUT", 12, minimum=0, maximum=60)
# How long the picker may wait for the preview before showing up without it;
# the probe keeps running in the background either way.
PREVIEW_WAIT = _int("PREVIEW_WAIT", 5, minimum=0, maximum=60)

LOG_LEVEL = _text("LOG_LEVEL", "INFO").upper()

# Short-video sites where a quality picker is pure friction: one file is all the
# site offers, so the bot downloads immediately at the best quality instead of
# asking. Comma-separated hosts (subdomains included).
DEFAULT_INSTANT_HOSTS = (
    "instagram.com,instagr.am,ig.me,tiktok.com,vm.tiktok.com,x.com,twitter.com"
)
INSTANT_HOSTS = tuple(
    host.strip().lower().lstrip(".")
    for host in _text("INSTANT_HOSTS", DEFAULT_INSTANT_HOSTS).split(",")
    if host.strip()
)

# Optional egress settings (needed where YouTube or a file host is blocked).
# Examples: socks5://127.0.0.1:9050 · http://user:pass@proxy:3128
PROXY_URL = _text("PROXY_URL") or None
# Media-only proxy: keep direct file downloads on the fast path while routing
# yt-dlp through something that can reach YouTube. Defaults to PROXY_URL.
YTDLP_PROXY = _text("YTDLP_PROXY") or PROXY_URL
# Instant-lane sites (Instagram & co) are often *blocked* by proxy exits and are
# several times faster without one. Empty = fetch them directly; set a value to
# force those hosts through a specific proxy.
INSTANT_PROXY = _text("INSTANT_PROXY") or None
# Netscape cookies.txt exported from a logged-in browser (fixes YouTube bot checks).
YTDLP_COOKIES_FILE = _text("YTDLP_COOKIES_FILE") or None

# --- Storage ----------------------------------------------------------------
# DATA_DIR keeps the database and temp files outside the code tree (Docker
# volume friendly, survives updates).
DATA_DIR = Path(_text("DATA_DIR") or str(BASE_DIR)).expanduser().resolve()
DOWNLOADS_DIR = DATA_DIR / "downloads"
DB_PATH = DATA_DIR / "bot_database.db"
HEARTBEAT_FILE = DATA_DIR / ".heartbeat"
YTDLP_CACHE_DIR = DATA_DIR / ".yt-dlp-cache"

for _directory in (DOWNLOADS_DIR, YTDLP_CACHE_DIR):
    _directory.mkdir(parents=True, exist_ok=True)

CHUNK_SIZE = DOWNLOAD_CHUNK_KB * 1024
UPLOAD_CHUNK_SIZE = UPLOAD_CHUNK_KB * 1024

# --- Project links ----------------------------------------------------------
GITHUB_REPO = "Opselon/Link-To-File-Telegram-Bot"
REPO_URL = f"https://github.com/{GITHUB_REPO}"
RELEASES_URL = f"{REPO_URL}/releases/latest"
ISSUES_URL = f"{REPO_URL}/issues"

# Optional: rename the bot on startup (BotFather display name).
# Empty = leave the stored name alone.
BOT_DISPLAY_NAME = _text("BOT_DISPLAY_NAME")
