"""All user-facing copy, in one place."""

from __future__ import annotations

import html

from linktofile.domain.models import ForceJoinPolicy, MediaAsset
from linktofile.infrastructure.system.version import VERSION
from linktofile.shared.utils import format_duration, format_size

BRAND = "Link → File"
TAGLINE = "Any link in. A file out."
REPO_URL = "https://github.com/Opselon/Link-To-File-Telegram-Bot"
RELEASES_URL = f"{REPO_URL}/releases/latest"
ISSUES_URL = f"{REPO_URL}/issues"


def welcome(user, *, is_admin: bool = False, cap_mb: int = 50) -> str:
    name = html.escape(user.first_name or "there")
    tail = "\n\n🛠 /admin — dashboard" if is_admin else ""
    return (
        f"⚡ <b>{BRAND}</b> — <i>{TAGLINE}</i>\n\n"
        f"Hey {name}! Send me a link and I'll hand the file back.\n\n"
        f"📦 Up to <b>{cap_mb} MB</b> per file · 🆓 free · ⚡ parallel downloads\n"
        "🎬 Video, 🎵 audio or any direct file link." + tail
    )


def help_text(cap_mb: int = 50) -> str:
    return (
        f"<b>How {BRAND} works</b>\n\n"
        "<b>1.</b> Paste any public link.\n"
        "<b>2.</b> Pick a quality (media links only).\n"
        "<b>3.</b> I download it, send the file and delete it from disk.\n\n"
        "<b>Commands</b>\n"
        "• /start — main menu\n"
        "• /help — this guide\n"
        "• /stats — your numbers\n"
        "• /cancel — stop your running downloads\n"
        "• /id — your Telegram ID\n\n"
        "<b>Good to know</b>\n"
        "• Private or DRM-protected media can't be fetched.\n"
        f"• Files over {cap_mb} MB (Telegram's cap) are refused up front.\n"
        "• Temporary files are deleted right after sending."
    )


def sites_text() -> str:
    return (
        "<b>✨ What I can fetch</b>\n\n"
        "<b>Media (auto-detected)</b>\n"
        "YouTube · Instagram · TikTok · X/Twitter · Facebook · Vimeo · Reddit · "
        "SoundCloud · Twitch · Pinterest · Dailymotion · Bilibili · VK · Rumble · "
        "Odysee · Streamable\n\n"
        "<b>Direct files</b>\n"
        "Any public http(s) link, including signed CDN links.\n\n"
        "<blockquote>If a page is supported by yt-dlp it usually just works. "
        "Not sure? Paste the link and see.</blockquote>"
    )


def stats_text(stats: dict, *, title: str = "📊 Your stats") -> str:
    total = stats.get("total", 0)
    completed = stats.get("completed", 0)
    return (
        f"<b>{title}</b>\n\n"
        f"📥 Downloads: <b>{total}</b>\n"
        f"✅ Completed: <b>{completed}</b>\n"
        f"⚠️ Failed: <b>{stats.get('failed', 0)}</b>\n"
        f"💾 Delivered: <b>{format_size(stats.get('bytes', 0))}</b>\n"
        f"🎯 Success rate: <b>{(completed / total * 100) if total else 0:.0f}%</b>"
    )


def admin_text(
    policy: ForceJoinPolicy,
    stats: dict,
    *,
    active: int,
    pending: int,
    disk_free: str,
    version: str = VERSION,
) -> str:
    channel = policy.channel or "not set"
    state = "🟢 on" if policy.enabled else "⚪️ off"
    return (
        "<b>🛠 Admin dashboard</b>\n\n"
        f"👥 Users: <b>{stats.get('users', 0)}</b>\n"
        f"📥 Downloads: <b>{stats.get('total', 0)}</b> (today: {stats.get('today', 0)})\n"
        f"✅ Completed: <b>{stats.get('completed', 0)}</b> · "
        f"⚠️ Failed: <b>{stats.get('failed', 0)}</b>\n"
        f"💾 Delivered: <b>{format_size(stats.get('bytes', 0))}</b>\n\n"
        f"⚙️ Workers: <b>{active}</b> busy · <b>{pending}</b> waiting\n"
        f"🗄 Disk free: <b>{disk_free}</b>\n"
        f"🔒 Force join: <b>{state}</b> · 📢 {html.escape(channel)}\n"
        f"🏷 Version: <b>{version}</b>"
    )


def join_text(policy: ForceJoinPolicy) -> str:
    channel = html.escape(policy.channel or "")
    return (
        "🔒 <b>One quick step</b>\n\n"
        f"Join {channel} to use the bot, then tap <b>I joined</b>."
    )


def join_unavailable_text() -> str:
    return (
        "🔒 <b>Join required</b>\n\n"
        "The channel isn't reachable right now. Please try again in a minute."
    )


def rate_limited_text(seconds: int) -> str:
    return f"⏳ Slow down a touch — try again in <b>{seconds}s</b>."


def queue_full_text() -> str:
    return "🚦 The queue is full right now. Try again in a moment."


def too_many_jobs_text() -> str:
    return "⏳ You already have downloads in flight. Finish or cancel one first."


def blocked_url_text() -> str:
    return "🚫 That link points to a private or local address, so I can't fetch it."


def cancel_text() -> str:
    return "🛑 <b>Cancelled.</b> Nothing is left running."


def missing_download_text() -> str:
    return "⌛️ That download is gone. Send the link again."


def done_caption(asset: MediaAsset) -> str:
    bits = [f"✅ <b>{html.escape(asset.title[:120])}</b>", "", f"📦 {format_size(asset.size)}"]
    if asset.height:
        bits.append(f"🎞 {asset.height}p")
    if asset.duration:
        bits.append(f"⏱ {format_duration(asset.duration)}")
    if asset.source:
        bits.append(f"🔗 <i>{html.escape(asset.source)}</i>")
    return " · ".join(bits[:2]) + "\n" + " · ".join(bits[2:])


def progress_text(header: str, bar: str) -> str:
    return f"{header}\n\n{bar}"


def queued_text(header: str, position: int) -> str:
    if position <= 0:
        return f"{header}\n\n⏳ <b>Queued</b>"
    return f"{header}\n\n⏳ <b>Queued</b> · {position} ahead of you"
