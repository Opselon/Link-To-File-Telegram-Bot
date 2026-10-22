"""All user-facing copy, in one place.

Every public function takes a keyword-only ``lang`` and renders through the
catalog in :mod:`linktofile.infrastructure.telegram.i18n`, so English stays the
default and Persian is one argument away. The RTL mark is added once, at the top
level of each message — never per fragment, or it would pile up in nested cards.
"""

from __future__ import annotations

import html
from pathlib import Path
from typing import Optional, Sequence
from urllib.parse import urlparse

from linktofile.domain.models import (
    DownloadStatus,
    DownloadTicket,
    ForceJoinPolicy,
    MediaAsset,
    MediaInfo,
    Quality,
    QualityKind,
)
from linktofile.infrastructure.system.version import VERSION
from linktofile.infrastructure.telegram.i18n import rlm, t
from linktofile.shared.utils import format_duration, format_size

BRAND = "Link → File"
TAGLINE = "Any link in. A file out."
REPO_URL = "https://github.com/Opselon/Link-To-File-Telegram-Bot"
RELEASES_URL = f"{REPO_URL}/releases/latest"
ISSUES_URL = f"{REPO_URL}/issues"


def _rtl(body: str, lang: str) -> str:
    """Prefix a complete message with the RTL mark when it is Persian."""
    return rlm(lang) + body


def welcome(user, *, is_admin: bool = False, cap_mb: int = 50, lang: str = "en") -> str:
    name = html.escape(user.first_name or t("welcome_fallback_name", lang))
    tail = t("admin_tail", lang) if is_admin else ""
    return _rtl(
        t(
            "welcome",
            lang,
            brand=BRAND,
            tagline=t("tagline", lang),
            name=name,
            cap_mb=cap_mb,
            admin_tail=tail,
        ),
        lang,
    )


def help_text(cap_mb: int = 50, *, lang: str = "en") -> str:
    return _rtl(t("help", lang, brand=BRAND, cap_mb=cap_mb), lang)


def sites_text(*, lang: str = "en") -> str:
    return _rtl(t("sites", lang), lang)


def stats_text(stats: dict, *, title: Optional[str] = None, lang: str = "en") -> str:
    total = stats.get("total", 0)
    completed = stats.get("completed", 0)
    return _rtl(
        t(
            "stats",
            lang,
            title=title if title is not None else t("stats_title", lang),
            total=total,
            completed=completed,
            failed=stats.get("failed", 0),
            bytes=format_size(stats.get("bytes", 0)),
            rate=f"{(completed / total * 100) if total else 0:.0f}%",
        ),
        lang,
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
    lang: str = "en",
) -> str:
    channel = policy.channel or t("channel_not_set", lang)
    state = t("force_state_on", lang) if policy.enabled else t("force_state_off", lang)
    memory_line = (
        t("admin_memory", lang, memory_mb=memory_mb, uptime=uptime) if memory_mb else ""
    )
    return _rtl(
        t(
            "admin",
            lang,
            users=stats.get("users", 0),
            total=stats.get("total", 0),
            today=stats.get("today", 0),
            completed=stats.get("completed", 0),
            failed=stats.get("failed", 0),
            bytes=format_size(stats.get("bytes", 0)),
            active=active,
            pending=pending,
            queued=stats.get("queued", 0),
            downloading=stats.get("downloading", 0),
            uploading=stats.get("uploading", 0),
            resumed=resumed,
            errors=failures_today,
            disk=disk_free,
            temp=temp_used,
            memory_line=memory_line,
            force_join=state,
            channel=html.escape(channel),
            version=version,
        ),
        lang,
    )


def admin_queue_text(
    tickets: Sequence[DownloadTicket],
    *,
    live: dict[int, str] | None = None,
    limit: int = 12,
    lang: str = "en",
) -> str:
    """Live queue with the age of each job — the stuck-download view."""
    live = live or {}
    if not tickets:
        return _rtl(t("queue_empty", lang), lang)
    lines = [t("queue_title", lang)]
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
        lines.append(t("queue_more", lang, n=len(tickets) - limit))
    lines.append(t("queue_footer", lang))
    return _rtl("\n".join(lines), lang)


def admin_errors_text(
    tickets: Sequence[DownloadTicket], *, limit: int = 8, lang: str = "en"
) -> str:
    """Recent failures with their machine-readable kind."""
    if not tickets:
        return _rtl(t("errors_empty", lang), lang)
    lines = [t("errors_title", lang)]
    for ticket in tickets[:limit]:
        reason = (ticket.error or t("errors_no_reason", lang)).replace("\n", " ")[:110]
        kind = ticket.error_kind or t("errors_unknown_kind", lang)
        lines.append(
            f"<code>#{ticket.id}</code> <b>{html.escape(kind)}</b>"
            f" · {html.escape(reason)}"
        )
    return _rtl("\n".join(lines), lang)


def duplicate_text(ticket_id: int, *, lang: str = "en") -> str:
    return _rtl(t("duplicate", lang, ticket=ticket_id), lang)


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


def join_text(policy: ForceJoinPolicy, *, lang: str = "en") -> str:
    return _rtl(t("join", lang, channel=html.escape(policy.channel or "")), lang)


def join_unavailable_text(*, lang: str = "en") -> str:
    return _rtl(t("join_unavailable", lang), lang)


def rate_limited_text(seconds: int, *, lang: str = "en") -> str:
    return _rtl(t("rate_limited", lang, seconds=seconds), lang)


def queue_full_text(*, lang: str = "en") -> str:
    return _rtl(t("queue_full", lang), lang)


def too_many_jobs_text(*, lang: str = "en") -> str:
    return _rtl(t("too_many_jobs", lang), lang)


def blocked_url_text(*, lang: str = "en") -> str:
    return _rtl(t("blocked_url", lang), lang)


def cancel_text(*, lang: str = "en") -> str:
    return _rtl(t("cancelled", lang), lang)


def missing_download_text(*, lang: str = "en") -> str:
    return _rtl(t("missing_download", lang), lang)


def not_a_link_text(*, lang: str = "en") -> str:
    return _rtl(t("not_a_link", lang), lang)


def nothing_running_text(*, lang: str = "en") -> str:
    return _rtl(t("nothing_running", lang), lang)


def your_id_text(user_id: int, *, lang: str = "en") -> str:
    return _rtl(t("your_id", lang, id=user_id), lang)


def version_text(version: str, *, lang: str = "en") -> str:
    return _rtl(t("version", lang, brand=BRAND, version=version), lang)


def admin_only_text(*, lang: str = "en") -> str:
    return _rtl(t("admin_only", lang), lang)


def channel_prompt_text(*, lang: str = "en") -> str:
    return _rtl(t("channel_prompt", lang), lang)


def channel_bad_text(*, lang: str = "en") -> str:
    return _rtl(t("channel_bad", lang), lang)


def channel_saved_text(channel: str, *, lang: str = "en") -> str:
    return _rtl(t("channel_saved", lang, channel=html.escape(channel)), lang)


def channel_saved_off_text(*, lang: str = "en") -> str:
    return t("channel_saved_off", lang)


def broadcast_too_long_text(*, lang: str = "en") -> str:
    return _rtl(t("broadcast_too_long", lang), lang)


def broadcast_bad_html_text(error: str, *, lang: str = "en") -> str:
    return _rtl(t("broadcast_bad_html", lang, error=error), lang)


def pick_quality_text(*, lang: str = "en") -> str:
    return _rtl(t("pick_quality", lang), lang)


def pick_quality_other_text(*, lang: str = "en") -> str:
    return _rtl(t("pick_quality_other", lang), lang)


def language_text(*, lang: str = "en") -> str:
    return _rtl(t("language", lang), lang)


def quality_header(quality: Optional[Quality], *, lang: str = "en") -> str:
    """The headline of a download card, before any progress is known."""
    if quality is None or quality.kind is QualityKind.VIDEO and quality.height is None:
        return t("header_fetching", lang)
    if quality.kind is QualityKind.AUDIO:
        return t("header_audio", lang, label=quality.label)
    return t("header_video", lang, label=quality.label)


def stage_text(stage: str, *, lang: str = "en") -> str:
    """Progress-bar stage words (the bar itself is layout, not copy)."""
    return t(f"stage_{stage}", lang)


def uploading_text(size: str, *, lang: str = "en") -> str:
    """Status line under the download header (a fragment, not a full card)."""
    return t("uploading", lang, size=size)


def failed_card_text(error: str, *, lang: str = "en") -> str:
    return _rtl(t("failed_card", lang, error=html.escape(error)), lang)


def resumed_failed_text(error: str, *, lang: str = "en") -> str:
    return _rtl(t("resumed_failed", lang, error=html.escape(error)), lang)


def done_caption(asset: MediaAsset, *, lang: str = "en") -> str:
    head = t("done_title", lang, title=html.escape(asset.title[:120]))
    meta = [t("done_size", lang, size=format_size(asset.size))]
    if asset.height:
        meta.append(t("done_quality", lang, height=asset.height))
    if asset.duration:
        meta.append(t("done_duration", lang, duration=format_duration(asset.duration)))
    if asset.source:
        meta.append(t("done_source", lang, source=html.escape(asset.source)))
    return _rtl(head + "\n" + " · ".join(meta), lang)


def progress_text(header: str, bar: str, *, lang: str = "en") -> str:
    # Pure layout: both fragments already came out of the catalog in ``lang``.
    return _rtl(f"{header}\n\n{bar}", lang)


def queued_text(header: str, position: int, *, lang: str = "en") -> str:
    if position <= 0:
        return _rtl(t("queued", lang, header=header), lang)
    return _rtl(t("queued_position", lang, header=header, position=position), lang)


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


def site_label(host: str, *, lang: str = "en") -> str:
    name = (host or "").lower()
    if name.startswith("www."):
        name = name[4:]
    if name in _SITE_LABELS:
        return _SITE_LABELS[name]
    for candidate, label in _SITE_LABELS.items():
        if name.endswith("." + candidate):
            return label
    return name or t("this_link", lang)


def instant_header(host: str, *, lang: str = "en") -> str:
    return t("instant_header", lang, site=html.escape(site_label(host, lang=lang)))


def instant_note(host: str, *, lang: str = "en") -> str:
    """One-line explanation shown under the queued card."""
    return t("instant_note", lang, site=site_label(host, lang=lang))


# --- v3.1: preview, history, broadcast --------------------------------------
STATUS_ICONS = {
    DownloadStatus.COMPLETED: "✅",
    DownloadStatus.FAILED: "⚠️",
    DownloadStatus.CANCELLED: "🛑",
    DownloadStatus.DOWNLOADING: "⬇️",
    DownloadStatus.UPLOADING: "⬆️",
    DownloadStatus.PENDING: "⏳",
}


def preview_caption(info: MediaInfo, *, lang: str = "en") -> str:
    """The card that sits above the quality picker, sourced from the probe."""
    title = html.escape(info.title[:120])
    meta: list[str] = []
    if info.uploader:
        meta.append(t("preview_uploader", lang, name=html.escape(info.uploader[:64])))
    if info.duration:
        meta.append(t("preview_duration", lang, duration=format_duration(info.duration)))
    if info.view_count:
        meta.append(t("preview_views", lang, views=f"{info.view_count:,}"))
    lines = [f"🎬 <b>{title}</b>"]
    if meta:
        lines.append(" · ".join(meta))
    lines += ["", t("pick_quality", lang)]
    return _rtl("\n".join(lines), lang)


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


def history_text(tickets: Sequence[DownloadTicket], *, lang: str = "en") -> str:
    if not tickets:
        return _rtl(t("history_empty", lang), lang)
    lines = [t("history_title", lang), ""]
    for number, ticket in enumerate(tickets, 1):
        icon = STATUS_ICONS.get(ticket.status, "•")
        size = f" · {format_size(ticket.size)}" if ticket.size else ""
        lines.append(f"{icon} {number}. {history_label(ticket)}{size}")
    lines += ["", t("history_retry", lang)]
    return _rtl("\n".join(lines), lang)


def broadcast_prompt_text(*, lang: str = "en") -> str:
    return _rtl(t("broadcast_prompt", lang), lang)


def broadcast_confirm_text(users: int, *, lang: str = "en") -> str:
    return _rtl(t("broadcast_confirm", lang, users=users), lang)


def broadcast_summary_text(total: int, sent: int, failed: int, *, lang: str = "en") -> str:
    return _rtl(t("broadcast_summary", lang, total=total, sent=sent, failed=failed), lang)
