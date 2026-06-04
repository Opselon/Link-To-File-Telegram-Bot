import asyncio
import sys
import logging
from bot import start_bot
from config import BOT_TOKEN

def main():
    if not BOT_TOKEN:
        print("Error: BOT_TOKEN is not set in environment variables or .env file.")
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
