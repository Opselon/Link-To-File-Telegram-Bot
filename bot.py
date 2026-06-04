import logging
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command
from aiogram.types import FSInputFile, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.filters.callback_data import CallbackData
import os
import asyncio
from typing import Optional

from config import BOT_TOKEN, ADMIN_ID, MAX_FILE_SIZE_MB
from db import db
from utils import is_valid_url, is_safe_url, check_rate_limit, format_size
from downloader import download_file, cleanup_file, DownloadError, is_youtube_url

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# We initialize Bot and Dispatcher inside start_bot or provide a way to mock them
bot = Bot(token=BOT_TOKEN if BOT_TOKEN else "123456:ABC-DEF1234ghIkl-zyx57W2v1u123ew11")
dp = Dispatcher()

class YTCallback(CallbackData, prefix="yt"):
    action: str
    download_id: int
    quality: Optional[str] = None

def get_yt_keyboard(download_id: int) -> InlineKeyboardMarkup:
    buttons = [
        [
            InlineKeyboardButton(text="🎬 4K", callback_data=YTCallback(action="mp4", download_id=download_id, quality="2160").pack()),
            InlineKeyboardButton(text="🎬 2K", callback_data=YTCallback(action="mp4", download_id=download_id, quality="1440").pack()),
            InlineKeyboardButton(text="🎬 1080p", callback_data=YTCallback(action="mp4", download_id=download_id, quality="1080").pack()),
        ],
        [
            InlineKeyboardButton(text="🎬 720p", callback_data=YTCallback(action="mp4", download_id=download_id, quality="720").pack()),
            InlineKeyboardButton(text="🎬 480p", callback_data=YTCallback(action="mp4", download_id=download_id, quality="480").pack()),
            InlineKeyboardButton(text="🎬 360p", callback_data=YTCallback(action="mp4", download_id=download_id, quality="360").pack()),
        ],
        [
            InlineKeyboardButton(text="🎬 240p", callback_data=YTCallback(action="mp4", download_id=download_id, quality="240").pack()),
            InlineKeyboardButton(text="🎬 144p", callback_data=YTCallback(action="mp4", download_id=download_id, quality="144").pack()),
            InlineKeyboardButton(text="🎬 Best", callback_data=YTCallback(action="mp4", download_id=download_id, quality="best").pack()),
        ],
        [
            InlineKeyboardButton(text="🎵 320kbps", callback_data=YTCallback(action="mp3", download_id=download_id, quality="320").pack()),
            InlineKeyboardButton(text="🎵 192kbps", callback_data=YTCallback(action="mp3", download_id=download_id, quality="192").pack()),
            InlineKeyboardButton(text="🎵 128kbps", callback_data=YTCallback(action="mp3", download_id=download_id, quality="128").pack()),
        ]
    ]
    return InlineKeyboardMarkup(inline_keyboard=buttons)

@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    db.add_user(message.from_user.id)
    await message.answer(
        "👋 Hello! I'm a File Downloader Bot.\n\n"
        "Send me a URL and I'll download it and send it back to you as a file.\n"
        f"Maximum file size: {MAX_FILE_SIZE_MB}MB."
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

    if is_youtube_url(url):
        download_id = db.add_download(user_id, url)
        await message.answer("📺 YouTube detected! Select format and quality:", reply_markup=get_yt_keyboard(download_id))
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
        if "Request Entity Too Large" in str(e) or "TelegramEntityTooLarge" in type(e).__name__:
             await status_msg.edit_text(
                 "❌ Error: File is too large for Telegram Bot API.\n\n"
                 "Standard Bots are limited to 50MB for uploading. "
                 "To send larger files (up to 2GB), you need to use a local Telegram Bot API server."
             )
        else:
            logger.exception(f"Unexpected error for user {user_id}")
            await status_msg.edit_text("❌ An unexpected error occurred.")
        db.update_download_status(download_id, 'failed')
    finally:
        if file_path:
            cleanup_file(file_path)

@dp.callback_query(YTCallback.filter())
async def process_yt_callback(callback: types.CallbackQuery, callback_data: YTCallback):
    download_id = callback_data.download_id
    # Get URL from DB
    conn = db.get_connection()
    row = conn.execute("SELECT url FROM downloads WHERE id = ?", (download_id,)).fetchone()
    conn.close()

    if not row:
        await callback.answer("❌ Download not found.")
        return

    url = row['url']
    user_id = callback.from_user.id

    ytdlp_options = {}
    quality = callback_data.quality

    if callback_data.action == "mp3":
        ytdlp_options = {
            'format': 'bestaudio/best',
            'postprocessors': [{
                'key': 'FFmpegExtractAudio',
                'preferredcodec': 'mp3',
                'preferredquality': quality if quality else '192',
            }],
        }
        await callback.message.edit_text(f"🎵 Downloading Audio (MP3 - {quality}kbps)...")
    else:
        if quality == "best":
            f_str = "bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best"
        else:
            f_str = f"bestvideo[height<={quality}][ext=mp4]+bestaudio[ext=m4a]/best[height<={quality}][ext=mp4]/best"

        ytdlp_options = {'format': f_str}
        await callback.message.edit_text(f"🎬 Downloading Video (MP4 - {quality}p)...")

    file_path = None
    try:
        file_path, size = await download_file(url, download_id, ytdlp_options)

        db.update_download_status(download_id, 'uploading', filename=os.path.basename(file_path), size=size)
        await callback.message.edit_text(f"📤 Uploading... ({format_size(size)})")

        document = FSInputFile(file_path)
        await callback.message.answer_document(document, caption=f"✅ Done! {format_size(size)}")

        db.update_download_status(download_id, 'completed')
        await callback.message.delete()

    except DownloadError as e:
        logger.error(f"Download error for user {user_id}: {e}")
        await callback.message.edit_text(f"❌ Error: {str(e)}")
        db.update_download_status(download_id, 'failed')
    except Exception as e:
        if "Request Entity Too Large" in str(e) or "TelegramEntityTooLarge" in type(e).__name__:
             await callback.message.edit_text(
                 "❌ Error: File is too large for Telegram Bot API.\n\n"
                 "Standard Bots are limited to 50MB for uploading. "
                 "To send larger files (up to 2GB), you need to use a local Telegram Bot API server."
             )
        else:
            logger.exception(f"Unexpected error for user {user_id}")
            await callback.message.edit_text("❌ An unexpected error occurred.")
        db.update_download_status(download_id, 'failed')
    finally:
        if file_path:
            cleanup_file(file_path)

async def start_bot():
    logger.info("Bot started...")
    await dp.start_polling(bot)
