# Telegram File Downloader Bot

A production-ready but simple Telegram File Downloader Bot in Python.

## Features

- **Direct File Downloads**: Supports any direct link to files.
- **YouTube Support**: Downloads videos from YouTube using `yt-dlp`.
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
- [aiogram 3.x](https://docs.aiogram.dev/)
- [aiohttp](https://docs.aiohttp.org/)
- [yt-dlp](https://github.com/yt-dlp/yt-dlp)
- SQLite (built-in)
- pytest (for testing)

## Project Structure

```text
main.py            # Entry point
bot.py             # Bot logic & handlers
downloader.py      # Download logic (streaming & yt-dlp)
db.py              # Database operations
config.py          # Configuration & environment variables
utils.py           # Helper functions (validation, safety)
requirements.txt   # Dependencies
.env.example       # Template for .env file
tests/             # Test suite
```

## Setup & Running

1. **Clone the repository** (or copy the files).

2. **Install dependencies**:
   ```bash
   pip install -r requirements.txt
   ```

3. **Configure environment variables**:
   ```bash
   cp .env.example .env
   ```
   Edit `.env` and fill in your `BOT_TOKEN`.

4. **Run the bot**:
   ```bash
   python main.py
   ```

## Running Tests

To run the full test suite:
```bash
PYTHONPATH=. pytest
```

## Security Note

The bot implements basic SSRF protection by checking literal IP addresses and common local hostnames. For high-security production environments, consider adding asynchronous DNS resolution to check the actual IP of the domain before requesting.
