import os
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

# Bot Configuration
BOT_TOKEN = os.getenv("BOT_TOKEN", "")
ADMIN_ID = int(os.getenv("ADMIN_ID", 0)) if os.getenv("ADMIN_ID") else None

# Downloader Configuration
MAX_FILE_SIZE_MB = int(os.getenv("MAX_FILE_SIZE_MB", 100))
DOWNLOAD_TIMEOUT = int(os.getenv("DOWNLOAD_TIMEOUT", 300))  # 5 minutes
CHUNK_SIZE = 1024 * 1024  # 1MB chunks

# Storage
BASE_DIR = Path(__file__).resolve().parent
DOWNLOADS_DIR = BASE_DIR / "downloads"
DOWNLOADS_DIR.mkdir(exist_ok=True)

DB_PATH = BASE_DIR / "bot_database.db"

# Rate Limiting (Simple in-memory for this example)
# {user_id: last_request_time}
RATE_LIMIT_SECONDS = 5
