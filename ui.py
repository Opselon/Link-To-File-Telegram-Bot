"""One place for every word and every button the user sees.

Styling rules
-------------
* ``primary`` (blue)   -> the main action on a card
* ``success`` (green)  -> positive / "go" actions
* ``danger``  (red)    -> cancel / destructive actions
* no style             -> secondary choices

Older aiogram versions do not know the ``style`` field, so buttons are built
through :func:`btn`, which degrades to a plain button instead of crashing.
"""

from __future__ import annotations

import html
from typing import Optional

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from config import (
    GITHUB_REPO,
    ISSUES_URL,
    MAX_FILE_SIZE_MB,
    RELEASES_URL,
    REPO_URL,
)
from utils import format_size

BRAND = "Link → File"
TAGLINE = "Any link in. A file out."

_STYLES = {"primary", "success", "danger"}
_STYLE_SUPPORTED = "style" in getattr(InlineKeyboardButton, "model_fields", {})

# Quality ladder used by the media menu (label, height).
VIDEO_QUALITIES = (
    ("4K", "2160"),
    ("2K", "1440"),
    ("1080p", "1080"),
    ("720p", "720"),
    ("480p", "480"),
    ("360p", "360"),
    ("240p", "240"),
    ("144p", "144"),
)
AUDIO_QUALITIES = ("320", "192", "128")


def btn(
    text: str,
    *,
    callback_data: Optional[str] = None,
    url: Optional[str] = None,
    style: Optional[str] = "primary",
    icon: Optional[str] = None,
) -> InlineKeyboardButton:
    """Build a styled inline button, safely on any aiogram 3.x.

    Every button gets a colour: ``primary`` is the default, callers override it
    with ``success`` for positive actions and ``danger`` for destructive ones.
    """
    kwargs: dict = {"text": text}
    if url:
        kwargs["url"] = url
    else:
        kwargs["callback_data"] = callback_data or "noop"
    if style in _STYLES and _STYLE_SUPPORTED:
        kwargs["style"] = style
    if icon and "icon_custom_emoji_id" in getattr(InlineKeyboardButton, "model_fields", {}):
        kwargs["icon_custom_emoji_id"] = icon
    return InlineKeyboardButton(**kwargs)


def _kb(rows: list[list[InlineKeyboardButton]]) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=rows)


# --- Keyboards --------------------------------------------------------------
def main_menu(*, is_admin: bool = False) -> InlineKeyboardMarkup:
    rows = [
        [btn("🚀 How it works", callback_data="menu:how", style="primary")],
        [
            btn("✨ Supported sites", callback_data="menu:sites"),
            btn("📊 My stats", callback_data="menu:stats"),
        ],
        [
            btn("⭐ Star on GitHub", url=REPO_URL, style="success"),
            btn("🐞 Report a bug", url=ISSUES_URL),
        ],
    ]
    if is_admin:
        rows.append([btn("🛠 Admin panel", callback_data="menu:admin")])
    return _kb(rows)


def quality_menu(download_id: int) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    top = VIDEO_QUALITIES[:3]
    rows.append(
        [
            btn(
                f"🎬 {label}",
                callback_data=f"q:mp4:{quality}:{download_id}",
                style="primary",
            )
            for label, quality in top
        ]
    )
    for start in (3, 6):
        rows.append(
            [
                btn(f"🎬 {label}", callback_data=f"q:mp4:{quality}:{download_id}")
                for label, quality in VIDEO_QUALITIES[start : start + 3]
            ]
        )
    rows.append(
        [
            btn(
                "⚡ Best quality",
                callback_data=f"q:mp4:best:{download_id}",
                style="success",
            )
        ]
    )
    rows.append(
        [
            btn(
                f"🎵 {quality} kbps",
                callback_data=f"q:mp3:{quality}:{download_id}",
                style="success" if quality == "320" else "primary",
            )
            for quality in AUDIO_QUALITIES
        ]
    )
    rows.append([btn("✖️ Cancel", callback_data=f"job:cancel:{download_id}", style="danger")])
    return _kb(rows)


def progress_menu(download_id: int) -> InlineKeyboardMarkup:
    return _kb(
        [[btn("✖️ Cancel download", callback_data=f"job:cancel:{download_id}", style="danger")]]
    )


def result_menu(download_id: int, *, media: bool = False) -> InlineKeyboardMarkup:
    row = [btn("🔁 Download again", callback_data=f"job:again:{download_id}", style="primary")]
    if media:
        row.append(btn("🎚 Change quality", callback_data=f"job:quality:{download_id}"))
    row.append(btn("🏠 Menu", callback_data="menu:home"))
    return _kb([row])


def error_menu(download_id: int) -> InlineKeyboardMarkup:
    return _kb(
        [
            [btn("🔁 Try again", callback_data=f"job:again:{download_id}", style="primary")],
            [
                btn("📖 Help", callback_data="menu:how"),
                btn("🏠 Menu", callback_data="menu:home"),
            ],
        ]
    )


def cancel_menu(download_id: int) -> InlineKeyboardMarkup:
    return _kb(
        [
            [btn("🔁 Start over", callback_data=f"job:again:{download_id}", style="primary")],
            [btn("🏠 Menu", callback_data="menu:home")],
        ]
    )


def help_menu() -> InlineKeyboardMarkup:
    return _kb(
        [
            [btn("🏠 Main menu", callback_data="menu:home", style="primary")],
            [
                btn("⭐ GitHub", url=REPO_URL, style="success"),
                btn("📦 Latest release", url=RELEASES_URL),
            ],
        ]
    )


def sites_menu() -> InlineKeyboardMarkup:
    return _kb(
        [
            [btn("🏠 Main menu", callback_data="menu:home", style="primary")],
            [btn("🐞 Request a site", url=f"{ISSUES_URL}/new", style="success")],
        ]
    )


def stats_menu() -> InlineKeyboardMarkup:
    return _kb(
        [
            [
                btn("🔄 Refresh", callback_data="menu:stats", style="primary"),
                btn("🏠 Menu", callback_data="menu:home"),
            ]
        ]
    )


def admin_menu() -> InlineKeyboardMarkup:
    return _kb(
        [
            [
                btn("🔄 Refresh", callback_data="menu:admin", style="primary"),
                btn("🏠 Menu", callback_data="menu:home"),
            ]
        ]
    )


# --- Texts ------------------------------------------------------------------
def _name(user) -> str:
    name = getattr(user, "first_name", None) or "there"
    return html.escape(name)


def welcome(user, *, is_admin: bool = False) -> str:
    return (
        f"<b>⚡ {BRAND}</b>\n"
        f"<i>{TAGLINE}</i>\n\n"
        f"Hey <b>{_name(user)}</b> — send me a link and I'll send the file back.\n\n"
        "<blockquote>"
        "🎬 YouTube, Instagram, TikTok, X and 1000+ sites via the extractor\n"
        "📄 Direct links: zip, pdf, mp4, mp3, images…\n"
        f"📦 Up to <b>{MAX_FILE_SIZE_MB} MB</b> per file · 🆓 free · ⚡ parallel downloads"
        "</blockquote>\n"
        "<b>Just paste a URL below to start.</b>"
    )


def help_text() -> str:
    return (
        f"<b>📖 How {BRAND} works</b>\n\n"
        "<b>1.</b> Paste a link in this chat.\n"
        "<b>2.</b> Pick a quality (media links only).\n"
        "<b>3.</b> Watch the live progress card and get your file.\n\n"
        "<b>Commands</b>\n"
        "• /start — main menu\n"
        "• /help — this guide\n"
        "• /stats — your download stats\n"
        "• /cancel — stop your running downloads\n"
        "• /id — show your Telegram ID\n\n"
        "<b>Good to know</b>\n"
        "• Private or DRM-protected media can't be fetched.\n"
        f"• Files over {MAX_FILE_SIZE_MB} MB are rejected before downloading.\n"
        "• Temporary files are deleted right after upload."
    )


def sites_text() -> str:
    return (
        "<b>✨ What I can download</b>\n\n"
        "<b>Media (auto-detected)</b>\n"
        "YouTube · Instagram · TikTok · X/Twitter · Facebook · Vimeo · "
        "Reddit · SoundCloud · Twitch · Pinterest · Dailymotion · Bilibili · "
        "VK · Rumble · Odysee · Streamable\n\n"
        "<b>Direct files</b>\n"
        "Any public http(s) link to a file, including signed CDN links.\n\n"
        "<blockquote>If a page is supported by yt-dlp, it usually just works. "
        "Not sure? Paste the link and see.</blockquote>"
    )


def stats_text(stats: dict, *, title: str = "📊 Your stats") -> str:
    completed = stats.get("completed", 0)
    failed = stats.get("failed", 0)
    total = stats.get("total", 0)
    success = (completed / total * 100) if total else 0
    return (
        f"<b>{title}</b>\n\n"
        f"📥 Downloads: <b>{total}</b>\n"
        f"✅ Completed: <b>{completed}</b>\n"
        f"⚠️ Failed: <b>{failed}</b>\n"
        f"💾 Data delivered: <b>{format_size(stats.get('bytes', 0))}</b>\n"
        f"🎯 Success rate: <b>{success:.0f}%</b>"
    )


def admin_text(stats: dict, *, uptime: str, queue_active: int, queue_pending: int, disk_free: str) -> str:
    return (
        "<b>🛠 Admin panel</b>\n\n"
        f"👥 Users: <b>{stats.get('users', 0)}</b>\n"
        f"📥 Downloads: <b>{stats.get('total', 0)}</b> "
        f"(today: {stats.get('today', 0)})\n"
        f"✅ Completed: <b>{stats.get('completed', 0)}</b>\n"
        f"⚠️ Failed: <b>{stats.get('failed', 0)}</b>\n"
        f"💾 Delivered: <b>{format_size(stats.get('bytes', 0))}</b>\n\n"
        f"⚙️ Workers busy: <b>{queue_active}</b> · waiting: <b>{queue_pending}</b>\n"
        f"🕒 Uptime: <b>{uptime}</b>\n"
        f"🗄 Disk free: <b>{disk_free}</b>\n"
        f"🔗 Repository: {html.escape(GITHUB_REPO)}"
    )


def missing_download_text() -> str:
    return "❌ This download expired. Send the link again, please."


def rate_limited_text(seconds: int) -> str:
    return f"⏳ Easy there — try again in <b>{max(1, seconds)}s</b>."


def queue_full_text() -> str:
    return "🚦 The queue is full right now. Give it a minute and try again."


def blocked_url_text() -> str:
    return "🚫 That link points to a private or local address, so I can't fetch it."


def cancel_text() -> str:
    return "✖️ <b>Download cancelled.</b>"


def done_caption(result) -> str:
    lines = [f"✅ <b>{html.escape(result.title)}</b>", "", f"📦 {format_size(result.size)}"]
    if result.duration:
        from utils import format_duration

        lines.append(f"⏱ {format_duration(result.duration)}")
    lines.append(f"🤖 <i>{BRAND}</i>")
    return "\n".join(lines)
