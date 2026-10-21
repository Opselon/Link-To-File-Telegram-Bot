"""Link → File entry point: CLI flags, logging, lifecycle, polling."""

from __future__ import annotations

import argparse
import asyncio
import logging
import shutil
import sys
from logging.handlers import RotatingFileHandler

from linktofile.config import (
    BOT_TOKEN,
    DATA_DIR,
    DOWNLOADS_DIR,
    HEARTBEAT_FILE,
    LOG_LEVEL,
    QUEUE_CONCURRENCY,
    TELEGRAM_API_URL,
    UPLOAD_LIMIT_MB,
)
from linktofile.infrastructure.system.version import VERSION

logger = logging.getLogger("linktofile")


def setup_logging(level: str | None = None) -> None:
    formatter = logging.Formatter(
        "%(asctime)s | %(levelname)-7s | %(name)s | %(message)s", datefmt="%H:%M:%S"
    )
    handlers: list[logging.Handler] = [logging.StreamHandler(sys.stdout)]
    try:
        handlers.append(
            RotatingFileHandler(
                DATA_DIR / "bot.log", maxBytes=5 * 1024 * 1024, backupCount=3, encoding="utf-8"
            )
        )
    except OSError:
        pass
    logging.basicConfig(
        level=getattr(logging, (level or LOG_LEVEL).upper(), logging.INFO),
        handlers=handlers,
        force=True,
    )
    for handler in handlers:
        handler.setFormatter(formatter)
    for noisy in ("aiogram.event", "aiohttp.access"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


def cleanup_downloads() -> None:
    """A crash can leave temp files behind; clear them on boot."""
    if not DOWNLOADS_DIR.exists():
        return
    removed = 0
    for item in DOWNLOADS_DIR.iterdir():
        try:
            if item.is_dir():
                shutil.rmtree(item)
            else:
                item.unlink()
            removed += 1
        except OSError as exc:
            logger.warning("Could not clean %s: %s", item.name, exc)
    if removed:
        logger.info("Cleaned %s leftover download artifact(s).", removed)


def print_banner() -> None:
    logger.info("⚡ Link → File v%s", VERSION)
    logger.info(
        "   data=%s · workers=%s · cap=%sMB", DATA_DIR, QUEUE_CONCURRENCY, UPLOAD_LIMIT_MB
    )
    if TELEGRAM_API_URL:
        logger.info("   Bot API: %s", TELEGRAM_API_URL)
    if not shutil.which("ffmpeg"):
        logger.warning("FFmpeg not found: YouTube/Instagram merging will fail.")


async def heartbeat(stop: asyncio.Event) -> None:
    """Touch a file so container health checks can see the loop is alive."""
    while not stop.is_set():
        try:
            HEARTBEAT_FILE.write_text(str(int(asyncio.get_running_loop().time())))
        except OSError:
            pass
        try:
            await asyncio.wait_for(stop.wait(), timeout=15)
        except asyncio.TimeoutError:
            continue


async def run_bot() -> None:
    from linktofile.bootstrap import build_application

    app = build_application()
    beat_stop = asyncio.Event()
    beat = asyncio.create_task(heartbeat(beat_stop))
    try:
        await app.dispatcher.start_polling(
            app.bot,
            allowed_updates=app.dispatcher.resolve_used_update_types(),
            handle_signals=True,
        )
    finally:
        beat_stop.set()
        await asyncio.gather(beat, return_exceptions=True)
        await app.close()


async def check_environment() -> int:
    """`--check`: validate token, ffmpeg and storage before starting."""
    import aiohttp

    ok = True
    if not BOT_TOKEN:
        print("✖ BOT_TOKEN is not set")
        return 1
    try:
        async with aiohttp.ClientSession() as session:
            url = f"{TELEGRAM_API_URL or 'https://api.telegram.org'}/bot{BOT_TOKEN}/getMe"
            async with session.get(url, timeout=aiohttp.ClientTimeout(total=15)) as response:
                payload = await response.json()
        if payload.get("ok"):
            me = payload["result"]
            print(f"✔ Telegram token valid: @{me.get('username')} ({me.get('id')})")
        else:
            print(f"✖ Telegram rejected the token: {payload.get('description')}")
            ok = False
    except Exception as exc:  # noqa: BLE001
        print(f"✖ Could not reach Telegram: {exc}")
        ok = False

    if shutil.which("ffmpeg"):
        print("✔ ffmpeg found")
    else:
        print("⚠ ffmpeg missing — media merging will fail (apk add ffmpeg)")
    try:
        probe = DATA_DIR / ".write-test"
        probe.write_text("ok")
        probe.unlink()
        print(f"✔ {DATA_DIR} is writable")
    except OSError as exc:
        print(f"✖ {DATA_DIR} is not writable: {exc}")
        ok = False

    from linktofile.infrastructure.persistence.sqlite import Database

    try:
        Database()
        print("✔ Database reachable")
    except Exception as exc:  # noqa: BLE001
        print(f"✖ Database error: {exc}")
        ok = False

    print(f"✔ Delivery cap: {UPLOAD_LIMIT_MB} MB · version {VERSION}")
    return 0 if ok else 1


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="linktofile", description="Link → File: a fast Telegram downloader bot."
    )
    parser.add_argument("--setup", action="store_true", help="run the configuration wizard")
    parser.add_argument("--check", action="store_true", help="validate token, ffmpeg and storage")
    parser.add_argument("--update", action="store_true", help="update to the latest release")
    parser.add_argument("--version", action="store_true", help="print the version")
    args = parser.parse_args()

    if args.version:
        print(f"Link → File v{VERSION}")
        return

    if args.update:
        from linktofile.infrastructure.system.updater import update_system

        update_system()
        return

    setup_logging()

    if args.check:
        sys.exit(asyncio.run(check_environment()))

    if args.setup or not BOT_TOKEN:
        if args.setup or sys.stdin.isatty():
            from linktofile.cli.wizard import run_wizard

            try:
                run_wizard()
            except KeyboardInterrupt:
                print("\nSetup cancelled.")
                sys.exit(1)
            if args.setup:
                return
        if not BOT_TOKEN:
            print(
                "✖ BOT_TOKEN is missing.\n\n"
                "Get one from @BotFather, then either:\n"
                "  • run: python main.py --setup\n"
                "  • or put BOT_TOKEN=123:ABC… in a .env file next to main.py\n"
                "  • or run the Docker installer: curl -fsSL "
                "https://raw.githubusercontent.com/Opselon/Link-To-File-Telegram-Bot/main/install.sh "
                "| bash"
            )
            sys.exit(2)

    cleanup_downloads()
    print_banner()

    try:
        asyncio.run(run_bot())
    except KeyboardInterrupt:
        print("\nBot stopped.")
    except Exception:
        logging.getLogger(__name__).exception("Fatal error")
        sys.exit(1)


if __name__ == "__main__":
    main()
