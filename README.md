# Telegram File Downloader Bot

A production-ready but simple Telegram File Downloader Bot in Python.

## Features

- **🚀 Modern CLI Wizard**: Interactive, beautiful terminal setup experience.
- **⚡ Easy Updates**: One-command safe update system with automatic backups.
- **Direct File Downloads**: Supports any direct link to files.
- **YouTube & Instagram Support**: Downloads videos from YouTube and Instagram (Reels, Posts, Stories) using `yt-dlp`.
- **Custom Bot API Support**: Supports local Telegram Bot API servers for uploading files up to 2GB.
- **Async & Streaming**: Uses `aiogram 3.x` and `aiohttp` with streaming (no high RAM usage).
- **SQLite Database**: Keeps track of users and download history.
- **Security**:
  - Blocks private/local IP ranges.
  - Configurable maximum file size limit.
  - Simple rate limiting per user.
- **Auto-cleanup**: Deletes temporary files after successful upload or on failure.
- **Comprehensive Tests**: Includes End-to-End tests for all core components.

## Tech Stack

- Python 3.11+
- [FFmpeg](https://ffmpeg.org/) (Required for YouTube downloads)
- [aiogram 3.x](https://docs.aiogram.dev/)
- [aiohttp](https://docs.aiohttp.org/)
- [rich](https://github.com/Textualize/rich) (for modern CLI UX)
- [yt-dlp](https://github.com/yt-dlp/yt-dlp)
- SQLite (built-in)
- pytest (for testing)

## Project Structure

```text
main.py            # Entry point (Handles CLI args & Bot start)
bot.py             # Bot logic & handlers
downloader.py      # Download logic (streaming & yt-dlp)
db.py              # Database operations
config.py          # Configuration & environment variables
cli/               # Modern setup wizard logic
core/              # Update system & version management
utils.py           # Helper functions (validation, safety)
requirements.txt   # Dependencies
.env.example       # Template for .env file
tests/             # Test suite
```

## Setup & Running

1. **Clone the repository**:
   ```bash
   git clone https://github.com/Opselon/Link-To-File-Telegram-Bot
   cd Link-To-File-Telegram-Bot
   ```

2. **Install System Dependencies**:
   On Ubuntu/Debian:
   ```bash
   sudo apt update && sudo apt install ffmpeg -y
   ```

3. **Install Python dependencies**:
   ```bash
   pip install -r requirements.txt
   ```

4. **Launch the Easy Setup Wizard**:
   The first time you run the bot, it will automatically launch the interactive wizard to configure everything for you.
   ```bash
   python main.py
   ```

## Updating

Keep your bot up to date with a single command. This will safely pull the latest code, backup your current configuration, and run any necessary migrations.
```bash
python main.py --update
```

## Running Tests

To run the full test suite:
```bash
PYTHONPATH=. pytest
```

## Security Note

The bot implements basic SSRF protection by checking literal IP addresses and common local hostnames. For high-security production environments, consider adding asynchronous DNS resolution to check the actual IP of the domain before requesting.
