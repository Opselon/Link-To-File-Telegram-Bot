import logging
from aiogram import Bot, Dispatcher, types, F, html
from aiogram.filters import Command
from aiogram.types import FSInputFile, InlineKeyboardMarkup, InlineKeyboardButton, Message
from aiogram.filters.callback_data import CallbackData
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.client.telegram import TelegramAPIServer
import os
import asyncio
import time
from typing import Optional

from config import BOT_TOKEN, ADMIN_ID, MAX_FILE_SIZE_MB, TELEGRAM_API_URL
from db import db
from utils import is_valid_url, is_safe_url, check_rate_limit, format_size, get_progress_bar
from downloader import download_file, cleanup_file, DownloadError, is_youtube_url, is_instagram_url
from queue_manager import download_queue

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# Initialize session if custom API URL is provided
session = None
if TELEGRAM_API_URL:
    session = AiohttpSession(
        api=TelegramAPIServer.from_base(TELEGRAM_API_URL)
    )

# We initialize Bot and Dispatcher inside start_bot or provide a way to mock them
bot = Bot(
    token=BOT_TOKEN if BOT_TOKEN else "123456:ABC-DEF1234ghIkl-zyx57W2v1u123ew11",
    session=session
)
dp = Dispatcher()

class YTCallback(CallbackData, prefix="yt"):
    action: str
    download_id: int
    quality: Optional[str] = None

class ProgressUpdater:
    def __init__(self, message: Message, prefix: str):
        self.message = message
        self.prefix = prefix
        self.last_update = 0
        self.update_interval = 2.0  # Update every 2 seconds to avoid rate limits
        try:
            self.loop = asyncio.get_running_loop()
        except RuntimeError:
            self.loop = asyncio.get_event_loop()

    def __call__(self, downloaded: int, total: int, speed: float, eta: float):
        current_time = time.time()
        if current_time - self.last_update < self.update_interval:
            return

        self.last_update = current_time
        progress_text = get_progress_bar(downloaded, total, speed, eta)
        full_text = f"{self.prefix}\n\n{progress_text}"

        # Use run_coroutine_threadsafe for thread safety (yt-dlp runs in a thread)
        asyncio.run_coroutine_threadsafe(self.safe_edit(full_text), self.loop)

    async def safe_edit(self, text: str):
        try:
            await self.message.edit_text(text, parse_mode="HTML")
        except Exception:
            # Ignore errors like "message is not modified" or rate limits
            pass

async def send_file(message: Message, file_path: str, caption: str, parse_mode: str = "HTML"):
    """Sends a file as video, audio, or document based on its extension."""
    ext = os.path.splitext(file_path)[1].lower()
    document = FSInputFile(file_path)

    if ext in ['.mp4', '.mkv', '.mov', '.avi']:
        await message.answer_video(document, caption=caption, parse_mode=parse_mode)
    elif ext in ['.mp3', '.m4a', '.wav', '.flac', '.ogg']:
        await message.answer_audio(document, caption=caption, parse_mode=parse_mode)
    else:
        await message.answer_document(document, caption=caption, parse_mode=parse_mode)

def get_yt_keyboard(download_id: int) -> InlineKeyboardMarkup:
    buttons = [
        [
            InlineKeyboardButton(text="🎬 4K", callback_data=YTCallback(action="mp4", download_id=download_id, quality="2160").pack(), style="primary"),
            InlineKeyboardButton(text="🎬 2K", callback_data=YTCallback(action="mp4", download_id=download_id, quality="1440").pack(), style="primary"),
            InlineKeyboardButton(text="🎬 1080p", callback_data=YTCallback(action="mp4", download_id=download_id, quality="1080").pack(), style="primary"),
        ],
        [
            InlineKeyboardButton(text="🎬 720p", callback_data=YTCallback(action="mp4", download_id=download_id, quality="720").pack()),
            InlineKeyboardButton(text="🎬 480p", callback_data=YTCallback(action="mp4", download_id=download_id, quality="480").pack()),
            InlineKeyboardButton(text="🎬 360p", callback_data=YTCallback(action="mp4", download_id=download_id, quality="360").pack()),
        ],
        [
            InlineKeyboardButton(text="🎬 240p", callback_data=YTCallback(action="mp4", download_id=download_id, quality="240").pack()),
            InlineKeyboardButton(text="🎬 144p", callback_data=YTCallback(action="mp4", download_id=download_id, quality="144").pack()),
            InlineKeyboardButton(text="🎬 Best", callback_data=YTCallback(action="mp4", download_id=download_id, quality="best").pack(), style="success"),
        ],
        [
            InlineKeyboardButton(text="🎵 320kbps", callback_data=YTCallback(action="mp3", download_id=download_id, quality="320").pack(), style="primary"),
            InlineKeyboardButton(text="🎵 192kbps", callback_data=YTCallback(action="mp3", download_id=download_id, quality="192").pack()),
            InlineKeyboardButton(text="🎵 128kbps", callback_data=YTCallback(action="mp3", download_id=download_id, quality="128").pack()),
        ]
    ]
    return InlineKeyboardMarkup(inline_keyboard=buttons)

@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    db.add_user(message.from_user.id)
    await message.answer(
        "👋 <b>Hello! I'm a File Downloader Bot.</b>\n\n"
        "Send me any URL and I'll download it and send it back to you as a file.\n\n"
        "📺 <b>YouTube & Instagram:</b> Just paste the link!\n"
        "🌐 <b>Other Links:</b> Direct file links work best.\n\n"
        f"🚀 <b>Max file size:</b> {MAX_FILE_SIZE_MB}MB.\n"
        "💰 <b>Cost:</b> Completely Free for everyone!\n\n"
        "Use /help to see more options.",
        parse_mode="HTML"
    )

@dp.message(Command("help"))
async def cmd_help(message: types.Message):
    await message.answer(
        "📖 <b>How to use the Bot:</b>\n\n"
        "1. <b>Paste a Link:</b> Send any direct link to a file, or a YouTube/Instagram link.\n"
        "2. <b>Wait for Download:</b> I will show you the progress in real-time.\n"
        "3. <b>Receive File:</b> Once finished, I will send the file directly to you.\n\n"
        "🛠 <b>For Developers:</b>\n"
        "• This bot is open-source.\n"
        "• Supports custom Telegram Bot API servers for larger files (up to 2GB).\n"
        "• built with aiogram 3.x and yt-dlp.\n\n"
        "✅ <b>Free & Unlimited:</b> Enjoy fast parallel downloads!",
        parse_mode="HTML"
    )

async def execute_download(message: Message, status_msg: Message, url: str, download_id: int, prefix: str, ytdlp_options: Optional[dict] = None):
    """Core logic to download, upload and cleanup a file. Runs inside the queue."""
    file_path = None
    user_id = message.from_user.id
    progress_updater = ProgressUpdater(status_msg, prefix)

    try:
        await status_msg.edit_text(f"{prefix}\n⏳ <b>Starting...</b>", parse_mode="HTML")
        file_path, size, title = await download_file(url, download_id, ytdlp_options, progress_callback=progress_updater)

        db.update_download_status(download_id, 'uploading', filename=os.path.basename(file_path), size=size)
        await status_msg.edit_text(f"{prefix}\n📤 <b>Uploading...</b> ({format_size(size)})", parse_mode="HTML")

        ext = os.path.splitext(file_path)[1].lower()
        if ext in ['.mp3', '.m4a', '.wav', '.flac', '.ogg']:
            caption = f"🎵 <b>{html.quote(title)}</b>\n\n✅ Done! {format_size(size)}"
        elif ext in ['.mp4', '.mkv', '.mov', '.avi']:
            caption = f"🎬 <b>{html.quote(title)}</b>\n\n✅ Done! {format_size(size)}"
        else:
            caption = f"📄 <b>{html.quote(title)}</b>\n\n✅ Done! {format_size(size)}"

        await send_file(message, file_path, caption=caption)

        db.update_download_status(download_id, 'completed')
        try:
            await status_msg.delete()
        except:
            pass

    except DownloadError as e:
        logger.error(f"Download error for user {user_id}: {e}")
        try:
            await status_msg.edit_text(f"❌ Error: {str(e)}")
        except:
            await message.answer(f"❌ Error: {str(e)}")
        db.update_download_status(download_id, 'failed')
    except Exception as e:
        if "Request Entity Too Large" in str(e) or "TelegramEntityTooLarge" in type(e).__name__:
             error_text = (
                 "❌ Error: File is too large for Telegram Bot API.\n\n"
                 "Standard Bots are limited to 50MB for uploading. "
                 "To send larger files (up to 2GB), you need to use a local Telegram Bot API server."
             )
        else:
            logger.exception(f"Unexpected error for user {user_id}")
            error_text = "❌ An unexpected error occurred."

        try:
            await status_msg.edit_text(error_text)
        except:
            await message.answer(error_text)
        db.update_download_status(download_id, 'failed')
    finally:
        if file_path:
            cleanup_file(file_path)

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

    if is_instagram_url(url):
        download_id = db.add_download(user_id, url)
        status_msg = await message.answer("📸 <b>Instagram Link Detected!</b>\n⏳ <i>Added to queue...</i>", parse_mode="HTML")
        await download_queue.add_task(
            execute_download,
            message,
            status_msg,
            url,
            download_id,
            "📸 <b>Instagram Download</b>"
        )
        return

    download_id = db.add_download(user_id, url)
    status_msg = await message.answer("🔍 <b>Link Detected!</b>\n⏳ <i>Added to queue...</i>", parse_mode="HTML")
    await download_queue.add_task(
        execute_download,
        message,
        status_msg,
        url,
        download_id,
        "⏳ <b>File Download</b>"
    )

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
        prefix = f"🎵 <b>Audio Download</b> (MP3 - {quality}kbps)"
    else:
        if quality == "best":
            f_str = "bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best"
        else:
            f_str = f"bestvideo[height<={quality}][ext=mp4]+bestaudio[ext=m4a]/best[height<={quality}][ext=mp4]/best"

        ytdlp_options = {'format': f_str}
        prefix = f"🎬 <b>Video Download</b> (MP4 - {quality}p)"

    await callback.message.edit_text(f"{prefix}\n⏳ <i>Added to queue...</i>", parse_mode="HTML")

    await download_queue.add_task(
        execute_download,
        callback.message,
        callback.message,
        url,
        download_id,
        prefix,
        ytdlp_options
    )
    await callback.answer("Added to download queue!")

async def start_bot():
    logger.info("Bot started...")
    download_queue.start()
    try:
        await dp.start_polling(bot)
    finally:
        await download_queue.stop()
