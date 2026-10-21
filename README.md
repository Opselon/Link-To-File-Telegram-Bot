# ⚡ Link → File

**Any link in. A file out.** A fast, good-looking Telegram downloader bot — paste a URL and get the file back.

[![CI](https://github.com/Opselon/Link-To-File-Telegram-Bot/actions/workflows/ci.yml/badge.svg)](https://github.com/Opselon/Link-To-File-Telegram-Bot/actions/workflows/ci.yml)
[![Release](https://img.shields.io/github/v/release/Opselon/Link-To-File-Telegram-Bot?display_name=tag&sort=semver)](https://github.com/Opselon/Link-To-File-Telegram-Bot/releases/latest)
[![Container](https://img.shields.io/badge/ghcr.io-link--to--file--telegram--bot-2496ED?logo=docker&logoColor=white)](https://github.com/Opselon/Link-To-File-Telegram-Bot/pkgs/container/link-to-file-telegram-bot)
[![License](https://img.shields.io/badge/license-MIT-green)](LICENSE)

---

## Install

### One line (recommended)

```bash
curl -fsSL https://raw.githubusercontent.com/Opselon/Link-To-File-Telegram-Bot/main/install.sh | bash
```

The installer detects Docker (preferred) or Python 3.10+, asks for your bot token, writes a `0600` `.env`, and starts the bot. Fully unattended:

```bash
BOT_TOKEN=123456:ABC… ADMIN_ID=123456789 \
  curl -fsSL https://raw.githubusercontent.com/Opselon/Link-To-File-Telegram-Bot/main/install.sh | bash
```

### Docker Compose

```bash
git clone https://github.com/Opselon/Link-To-File-Telegram-Bot && cd Link-To-File-Telegram-Bot
cp .env.example .env        # put BOT_TOKEN + ADMIN_ID in it
docker compose up -d        # pulls ghcr.io/opselon/link-to-file-telegram-bot:latest
```

### Bare metal

```bash
git clone https://github.com/Opselon/Link-To-File-Telegram-Bot && cd Link-To-File-Telegram-Bot
python3 -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt
python main.py --setup      # interactive wizard → .env
python main.py              # or: python main.py --check (token/ffmpeg/storage)
```

Docker: [get.docker.com](https://get.docker.com) · ffmpeg: `sudo apt install ffmpeg` (needed for YouTube/Instagram merging).

---

## What it does

| | |
|---|---|
| 🎬 **Media sites** | YouTube, Instagram, TikTok, X, Facebook, Vimeo, Reddit, SoundCloud, Twitch, Pinterest, Dailymotion, Bilibili, VK, Rumble… and anything else `yt-dlp` knows |
| 📄 **Direct files** | Any public http(s) link — zip, pdf, images, signed CDN URLs |
| 🎚 **Quality picker** | 4K → 144p, plus MP3 at 320/192/128 kbps |
| 📊 **Live card** | Real-time progress bar, speed, ETA, one-tap cancel |
| 🔁 **Recovery** | Retry / change quality / download again right on the result card |
| 🛡 **Safety** | SSRF guard, per-user rate limit, hard size caps, temporary files deleted after upload |

### What changed in v2

- **Every button is styled.** Native Telegram button colours (`primary` / `success` / `danger`) on the main menu, quality picker, progress, error, stats and help cards — no unstyled leftovers.
- **Faster.** 3 parallel download workers, one pooled keep-alive HTTP session, 256 KB streaming chunks, 8 concurrent yt-dlp fragments, throttled progress edits, WAL SQLite.
- **Lighter.** `yt-dlp` is imported lazily (startup and idle RAM stay small), test tooling moved out of runtime deps, non-root multi-arch image with a built-in health check.
- **Smarter.** A web page that isn't a file is handed to yt-dlp automatically; private/oversized media fails with a clear message instead of a stack trace.
- **Easier to run.** One-line installer, `docker compose up`, setup wizard, `--check` preflight, automatic GitHub releases with source archives and a published container image.

---

## Commands

| Command | What it does |
|---|---|
| `/start` | Main menu |
| `/help` | How it works |
| `/stats` | Your download stats |
| `/cancel` | Stop your running downloads |
| `/id` | Your Telegram ID / chat ID |
| `/version` | Bot version + latest release link |

Admins (`ADMIN_ID`) get an extra **🛠 Admin panel** button: users, downloads today, queue load, uptime, disk space.

---

## Configuration

All settings are environment variables (`.env` is read automatically). Only the first two are required.

| Variable | Default | Notes |
|---|---|---|
| `BOT_TOKEN` | — | From [@BotFather](https://t.me/BotFather) |
| `ADMIN_ID` | — | Your numeric Telegram ID |
| `MAX_FILE_SIZE_MB` | `2000` | Clamped to 1–2000 (Bot API maximum) |
| `DOWNLOAD_TIMEOUT` | `600` | Per-request timeout, seconds |
| `QUEUE_CONCURRENCY` | `3` | Parallel downloads (1–16) |
| `QUEUE_MAX_SIZE` | `200` | Waiting jobs before the bot says "busy" |
| `RATE_LIMIT_SECONDS` | `3` | Per-user cooldown |
| `DOWNLOAD_CHUNK_KB` | `256` | Streaming chunk size |
| `UPLOAD_CHUNK_KB` | `1024` | Upload chunk size |
| `YTDLP_FRAGMENTS` | `8` | Concurrent media fragments |
| `PROGRESS_INTERVAL_MS` | `1500` | Progress card refresh |
| `TELEGRAM_API_URL` | — | Self-hosted Bot API server |
| `PROXY_URL` | — | Egress proxy for all traffic, e.g. `socks5://127.0.0.1:9050` |
| `YTDLP_PROXY` | `PROXY_URL` | Media-only proxy (keeps direct downloads direct) |
| `YTDLP_COOKIES_FILE` | — | Netscape `cookies.txt` for bot-checked sites |
| `BOT_DISPLAY_NAME` | — | Optional BotFather display name |
| `MIN_FREE_DISK_MB` | `512` | Refuse new jobs below this free space |
| `TEMP_FILE_TTL` | `900` | Seconds before orphaned temp files are swept |
| `MAX_PENDING_PER_USER` | `2` | Per-user queue depth |
| `DATA_DIR` | project dir | Database, logs, temp files (`/data` in Docker) |
| `LOG_LEVEL` | `INFO` | `DEBUG` for troubleshooting |

### Blocked sites (YouTube bot check, geo-blocks)

Datacenter IPs often get *"Sign in to confirm you're not a bot"* from YouTube. Two supported fixes:

```env
# 1) route everything through a proxy (SOCKS5 or HTTP)
PROXY_URL=socks5://127.0.0.1:9050

# 2) or supply browser cookies for the extractor
YTDLP_COOKIES_FILE=/data/cookies.txt
```

Both are optional; without them everything else keeps working.

### Files larger than 50 MB

The cloud Bot API caps bot uploads at **50 MB**. To send up to 2 GB, run a [local Bot API server](https://github.com/tdlib/telegram-bot-api) and point `TELEGRAM_API_URL` at it (e.g. `http://127.0.0.1:8081`). The bot detects the higher limit and reports it in `/start`.

---

## Releases

Releases are automated. Bumping `core/version.py` on `main` (or pushing a `v*` tag) makes the **Release** workflow:

1. run the test suite,
2. create the git tag,
3. build and push a multi-arch image (`linux/amd64`, `linux/arm64`) to `ghcr.io/opselon/link-to-file-telegram-bot` with `latest`, `major.minor`, `version` and `sha` tags,
4. publish a GitHub release with generated notes, source `.zip`/`.tar.gz` and `SHA256SUMS.txt`.

```bash
docker pull ghcr.io/opselon/link-to-file-telegram-bot:latest
```

---

## Development

```bash
pip install -r requirements-dev.txt
ruff check --select E9,F .
PYTHONPATH=. pytest -q
python main.py --check     # token, ffmpeg, storage
```

```
bot.py            handlers, cards, job lifecycle
ui.py             every text + keyboard (single source of styling truth)
downloader.py     streaming HTTP + yt-dlp, cancel/retry, size caps
queue_manager.py  bounded queue with N workers
upload_utils.py   upload progress stream
db.py             SQLite (WAL)
config.py         env parsing with clamps
cli/wizard.py     setup wizard
core/updater.py   self-update for git installs
scripts/healthcheck.py  container health probe
```

Pull requests are welcome — keep `ruff check --select E9,F .` and `pytest` green.

---

## فارسی — راه‌اندازی سریع

```bash
curl -fsSL https://raw.githubusercontent.com/Opselon/Link-To-File-Telegram-Bot/main/install.sh | bash
```

۱. از [@BotFather](https://t.me/BotFather) یک ربات بسازید و توکن را بگیرید.
۲. اسکریپت را اجرا کنید و توکن را وارد کنید (یا `BOT_TOKEN=...` را قبل از اجرا ست کنید).
۳. آیدی عددی خودتان را از [@userinfobot](https://t.me/userinfobot) بگیرید و به‌عنوان `ADMIN_ID` وارد کنید.
۴. تمام؛ لینک را برای ربات بفرستید تا فایل را دریافت کنید.

- نصب با داکر: `docker compose up -d`
- اجرا بدون داکر: `python main.py --setup` و سپس `python main.py`
- برای فایل‌های بالای ۵۰ مگابایت به یک سرور محلی Bot API نیاز دارید (`TELEGRAM_API_URL`).

---

## License

MIT — see [LICENSE](LICENSE).
