import asyncio
import sys
import logging
import argparse
import shutil
from pathlib import Path

# Try to load version
try:
    from core.version import VERSION
except ImportError:
    VERSION = "unknown"

def main():
    parser = argparse.ArgumentParser(description="Telegram File Downloader Bot")
    parser.add_argument("--update", action="store_true", help="Update the bot to the latest version")
    parser.add_argument("--version", action="store_true", help="Show the current version")
    parser.add_argument("--setup", action="store_true", help="Run the configuration wizard")
    args = parser.parse_args()

    if args.version:
        print(f"Telegram File Downloader Bot v{VERSION}")
        return

    if args.update:
        from core.updater import update_system
        update_system()
        return

    if args.setup:
        from cli.wizard import run_wizard
        try:
            run_wizard()
        except KeyboardInterrupt:
            print("\nSetup cancelled.")
            sys.exit(1)
        return

    # Check if configured
    if not Path(".env").exists():
        from cli.wizard import run_wizard
        try:
            run_wizard()
        except KeyboardInterrupt:
            print("\nSetup cancelled.")
            sys.exit(1)

    # Check for ffmpeg
    if not shutil.which("ffmpeg"):
        logging.warning("FFmpeg not found in PATH. YouTube downloads will likely fail.")

    # Now we can safely import and run the bot
    from bot import start_bot
    from config import BOT_TOKEN, TELEGRAM_API_URL

    if not BOT_TOKEN:
        print("Error: BOT_TOKEN is not set. Please run the wizard using --setup or check your .env file.")
        sys.exit(1)

    if TELEGRAM_API_URL is None:
        print("Warning: TELEGRAM_API_URL is not set in .env.")
        print("It's highly recommended to run the setup wizard to ensure all settings are configured correctly.")
        print("Run: python main.py --setup")
        # Optional: Force run wizard if it's a new version that requires more config?
        # For now, just a warning as it might still work with defaults for some users.

    try:
        asyncio.run(start_bot())
    except KeyboardInterrupt:
        print("\nBot stopped.")
    except Exception as e:
        logging.exception("Fatal error")
        sys.exit(1)

if __name__ == "__main__":
    main()
