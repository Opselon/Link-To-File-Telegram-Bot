import logging
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command
from aiogram.types import FSInputFile
import os
import asyncio

from config import BOT_TOKEN, ADMIN_ID
from db import db
from utils import is_valid_url, is_safe_url, check_rate_limit, format_size
from downloader import download_file, cleanup_file, DownloadError

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# We initialize Bot and Dispatcher inside start_bot or provide a way to mock them
bot = Bot(token=BOT_TOKEN if BOT_TOKEN else "123456:ABC-DEF1234ghIkl-zyx57W2v1u123ew11")
dp = Dispatcher()

@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    db.add_user(message.from_user.id)
    await message.answer(
        "👋 Hello! I'm a File Downloader Bot.\n\n"
        "Send me a URL and I'll download it and send it back to you as a file.\n"
        "Maximum file size: 100MB (default)."
    )

@dp.message(F.text)
async def handle_url(message: types.Message):
    url = message.text.strip()
    user_id = message.from_user.id

    if not is_valid_url(url):
        return

    if not check_rate_limit(user_id):
        await message.answer("⚠️ Please wait a few seconds before sending another request.")
        return

    if not is_safe_url(url):
        await message.answer("❌ This URL is not allowed (private/local IP or invalid).")
        return

    status_msg = await message.answer("🔍 Checking...")
    download_id = db.add_download(user_id, url)
    file_path = None

    try:
        await status_msg.edit_text("⏳ Downloading...")
        file_path, size = await download_file(url, download_id)

        db.update_download_status(download_id, 'uploading', filename=os.path.basename(file_path), size=size)
        await status_msg.edit_text(f"📤 Uploading... ({format_size(size)})")

        document = FSInputFile(file_path)
        await message.answer_document(document, caption=f"✅ Done! {format_size(size)}")

        db.update_download_status(download_id, 'completed')
        await status_msg.delete()

    except DownloadError as e:
        logger.error(f"Download error for user {user_id}: {e}")
        await status_msg.edit_text(f"❌ Error: {str(e)}")
        db.update_download_status(download_id, 'failed')
    except Exception as e:
        logger.exception(f"Unexpected error for user {user_id}")
        await status_msg.edit_text("❌ An unexpected error occurred.")
        db.update_download_status(download_id, 'failed')
    finally:
        if file_path:
            cleanup_file(file_path)

async def start_bot():
    logger.info("Bot started...")
    await dp.start_polling(bot)
