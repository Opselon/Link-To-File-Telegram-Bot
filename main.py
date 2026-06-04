import asyncio
import sys
import logging
import argparse
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
    args = parser.parse_args()

    if args.version:
        print(f"Telegram File Downloader Bot v{VERSION}")
        return

    if args.update:
        from core.updater import update_system
        update_system()
        return

    # Check if configured
    if not Path(".env").exists():
        from cli.wizard import run_wizard
        try:
            run_wizard()
        except KeyboardInterrupt:
            print("\nSetup cancelled.")
            sys.exit(1)

    # Now we can safely import and run the bot
    from bot import start_bot
    from config import BOT_TOKEN

    if not BOT_TOKEN:
        print("Error: BOT_TOKEN is not set. Please run the wizard or check your .env file.")
        sys.exit(1)

    try:
        asyncio.run(start_bot())
    except KeyboardInterrupt:
        print("\nBot stopped.")
    except Exception as e:
        logging.exception("Fatal error")
        sys.exit(1)

if __name__ == "__main__":
    main()
