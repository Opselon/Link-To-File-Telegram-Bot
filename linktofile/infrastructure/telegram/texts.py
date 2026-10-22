"""All user-facing copy, in one place."""

from __future__ import annotations

import html
from pathlib import Path
from typing import Sequence
from urllib.parse import urlparse

from linktofile.domain.models import (
    DownloadStatus,
    DownloadTicket,
    ForceJoinPolicy,
    MediaAsset,
    MediaInfo,
)
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
        "<b>2.</b> Long video: pick a quality. Instagram, TikTok and X have no "
        "quality step — they download the moment you send the link.\n"
        "<b>3.</b> I download it, send the file and delete it from disk.\n\n"
        "<b>Commands</b>\n"
        "• /start — main menu\n"
        "• /help — this guide\n"
        "• /history — your recent downloads\n"
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
    memory_mb: int = 0,
    temp_used: str = "0 B",
    uptime: str = "",
    failures_today: int = 0,
    resumed: int = 0,
) -> str:
    channel = policy.channel or "not set"
    state = "🟢 on" if policy.enabled else "⚪️ off"
    queued = stats.get("queued", 0)
    downloading = stats.get("downloading", 0)
    uploading = stats.get("uploading", 0)
    return (
        "<b>🛠 Admin dashboard</b>\n\n"
        f"👥 Users: <b>{stats.get('users', 0)}</b>\n"
        f"📥 Downloads: <b>{stats.get('total', 0)}</b> (today: {stats.get('today', 0)})\n"
        f"✅ Completed: <b>{stats.get('completed', 0)}</b> · "
        f"⚠️ Failed: <b>{stats.get('failed', 0)}</b>\n"
        f"💾 Delivered: <b>{format_size(stats.get('bytes', 0))}</b>\n\n"
        f"⚙️ Workers: <b>{active}</b> busy · <b>{pending}</b> waiting\n"
        f"📦 In flight: <b>{queued}</b> queued · <b>{downloading}</b> downloading · "
        f"<b>{uploading}</b> uploading\n"
        f"🔁 Resumed this boot: <b>{resumed}</b> · ⚠️ Errors today: <b>{failures_today}</b>\n"
        f"🗄 Disk free: <b>{disk_free}</b> · temp: <b>{temp_used}</b>\n"
        + (f"🧠 RAM: <b>{memory_mb} MB</b> · ⏱ up: <b>{uptime}</b>\n" if memory_mb else "")
        + f"🔒 Force join: <b>{state}</b> · 📢 {html.escape(channel)}\n"
        f"🏷 Version: <b>{version}</b>"
    )


def admin_queue_text(
    tickets: Sequence[DownloadTicket],
    *,
    live: dict[int, str] | None = None,
    limit: int = 12,
) -> str:
    """Live queue with the age of each job — the stuck-download view."""
    live = live or {}
    if not tickets:
        return (
            "📦 <b>Queue</b>\n\n"
            "Nothing is queued or running right now."
        )
    lines = ["📦 <b>Queue</b>\n"]
    for ticket in tickets[:limit]:
        host = ""
        try:
            host = (urlparse(ticket.url).hostname or "")[:32]
        except ValueError:
            host = "?"
        state = live.get(ticket.id) or ticket.status.value
        age = _age(ticket.created_at)
        quality = ticket.quality.label if ticket.quality else "auto"
        lines.append(
            f"<code>#{ticket.id}</code> {html.escape(state)} · {html.escape(host)}"
            f" · {html.escape(quality)} · {age}"
        )
    if len(tickets) > limit:
        lines.append(f"…and {len(tickets) - limit} more")
    lines.append("\n<i>status comes from the live queue when the job is in memory.</i>")
    return "\n".join(lines)


def admin_errors_text(tickets: Sequence[DownloadTicket], *, limit: int = 8) -> str:
    """Recent failures with their machine-readable kind."""
    if not tickets:
        return "✅ <b>Errors</b>\n\nNo failed tickets recorded."
    lines = ["⚠️ <b>Recent failures</b>\n"]
    for ticket in tickets[:limit]:
        reason = (ticket.error or "no reason recorded").replace("\n", " ")[:110]
        lines.append(
            f"<code>#{ticket.id}</code> <b>{html.escape(ticket.error_kind or 'unknown')}</b>"
            f" · {html.escape(reason)}"
        )
    return "\n".join(lines)


def duplicate_text(ticket_id: int) -> str:
    return (
        "⏳ <b>Already on it</b>\n\n"
        f"That exact link is already queued as <code>#{ticket_id}</code>. "
        "I won't start it twice — cancel the running one first if you want a fresh copy."
    )


def _age(created_at: str | None) -> str:
    if not created_at:
        return "—"
    from datetime import datetime, timezone

    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S"):
        try:
            stamp = datetime.strptime(str(created_at)[:19], fmt).replace(tzinfo=timezone.utc)
        except ValueError:
            continue
        seconds = max(0, int((datetime.now(timezone.utc) - stamp).total_seconds()))
        if seconds < 60:
            return f"{seconds}s"
        if seconds < 3600:
            return f"{seconds // 60}m"
        return f"{seconds // 3600}h"
    return "—"


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


# --- v3.2: instant lane ------------------------------------------------------
# Short-video sites serve one file per post, so the quality picker is noise:
# the bot downloads straight away and says so on the card.
_SITE_LABELS = {
    "instagram.com": "Instagram",
    "instagr.am": "Instagram",
    "ig.me": "Instagram",
    "tiktok.com": "TikTok",
    "vm.tiktok.com": "TikTok",
    "twitter.com": "X",
    "x.com": "X",
}


def site_label(host: str) -> str:
    name = (host or "").lower()
    if name.startswith("www."):
        name = name[4:]
    if name in _SITE_LABELS:
        return _SITE_LABELS[name]
    for candidate, label in _SITE_LABELS.items():
        if name.endswith("." + candidate):
            return label
    return name or "this link"


def instant_header(host: str) -> str:
    return f"⚡ <b>{html.escape(site_label(host))}</b> · auto quality"


def instant_note(host: str) -> str:
    """One-line explanation shown under the queued card."""
    return (
        f"{site_label(host)} gives one file per post, so there is no quality to pick — "
        "I'm fetching the best version that fits the size cap."
    )


# --- v3.1: preview, history, broadcast --------------------------------------
STATUS_ICONS = {
    DownloadStatus.COMPLETED: "✅",
    DownloadStatus.FAILED: "⚠️",
    DownloadStatus.CANCELLED: "🛑",
    DownloadStatus.DOWNLOADING: "⬇️",
    DownloadStatus.UPLOADING: "⬆️",
    DownloadStatus.PENDING: "⏳",
}


def preview_caption(info: MediaInfo) -> str:
    """The card that sits above the quality picker, sourced from the probe."""
    title = html.escape(info.title[:120])
    meta: list[str] = []
    if info.uploader:
        meta.append(f"👤 {html.escape(info.uploader[:64])}")
    if info.duration:
        meta.append(f"⏱ {format_duration(info.duration)}")
    if info.view_count:
        meta.append(f"👁 {info.view_count:,}")
    lines = [f"🎬 <b>{title}</b>"]
    if meta:
        lines.append(" · ".join(meta))
    lines += ["", "🎚 <b>Pick a quality</b>"]
    return "\n".join(lines)


def history_label(ticket: DownloadTicket) -> str:
    """A short, safe label for a history row and its button."""
    label = (ticket.filename or "").strip()
    if not label:
        try:
            parsed = urlparse(ticket.url)
        except ValueError:
            parsed = None
        name = Path(parsed.path).name if parsed else ""
        label = name or (parsed.hostname if parsed else "") or "link"
    label = html.escape(label) or "link"
    return label if len(label) <= 34 else label[:33] + "…"


def history_text(tickets: Sequence[DownloadTicket]) -> str:
    if not tickets:
        return (
            "🕘 <b>History</b>\n\n"
            "Nothing here yet — send me a link and it will show up in this list."
        )
    lines = ["🕘 <b>Your last downloads</b>", ""]
    for number, ticket in enumerate(tickets, 1):
        icon = STATUS_ICONS.get(ticket.status, "•")
        size = f" · {format_size(ticket.size)}" if ticket.size else ""
        lines.append(f"{icon} {number}. {history_label(ticket)}{size}")
    lines += ["", "Tap a title below to fetch it again 🔁"]
    return "\n".join(lines)


def broadcast_prompt_text() -> str:
    return (
        "📣 <b>Broadcast</b>\n\n"
        "Send the message every user should receive. HTML is allowed, and you "
        "get a preview plus a confirmation before anything is sent."
    )


def broadcast_confirm_text(users: int) -> str:
    return (
        "📣 <b>Ready to send</b>\n\n"
        f"The message above goes to <b>{users}</b> user(s).\n\n"
        "Sending runs in the background — you'll get a summary here when it finishes."
    )


def broadcast_summary_text(total: int, sent: int, failed: int) -> str:
    return (
        "📣 <b>Broadcast finished</b>\n\n"
        f"✅ Delivered: <b>{sent}</b>\n"
        f"⚠️ Skipped/failed: <b>{failed}</b>\n"
        f"👥 Recipients: <b>{total}</b>"
    )
