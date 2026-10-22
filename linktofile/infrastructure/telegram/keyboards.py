"""Keyboards. Every screen has a way back — enforced by the tests."""

from __future__ import annotations

from typing import Optional, Sequence

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from linktofile.domain.models import DownloadTicket, ForceJoinPolicy, Quality
from linktofile.infrastructure.telegram import texts
from linktofile.infrastructure.telegram.callbacks import (
    AdminCB,
    JoinCB,
    Nav,
    QualityCB,
    TicketCB,
)

try:  # aiogram >= 3.25
    InlineKeyboardButton(text="x", callback_data="x", style="primary")
    _STYLED = True
except Exception:  # pragma: no cover - old aiogram
    _STYLED = False

VIDEO_QUALITIES: list[tuple[int, str]] = [
    (2160, "2160p"),
    (1440, "1440p"),
    (1080, "1080p"),
    (720, "720p"),
    (480, "480p"),
    (360, "360p"),
]
AUDIO_QUALITIES: list[int] = [320, 256, 192, 128, 64]


def btn(text: str, *, style: str = "primary", **kwargs) -> InlineKeyboardButton:
    if _STYLED:
        return InlineKeyboardButton(text=text, style=style, **kwargs)
    return InlineKeyboardButton(text=text, **kwargs)


def _back(action: str = "back") -> InlineKeyboardButton:
    label = "🏠 Menu" if action == "home" else "⬅️ Back"
    return btn(label, callback_data=Nav(action=action).pack(), style="primary")


def _close() -> InlineKeyboardButton:
    return btn("✖️ Close", callback_data=Nav(action="close").pack(), style="danger")


def main_menu(*, is_admin: bool = False) -> InlineKeyboardMarkup:
    rows = [
        [btn("✨ What I can fetch", callback_data=Nav(action="sites").pack())],
        [
            btn("📊 My stats", callback_data=Nav(action="stats").pack()),
            btn("❓ How it works", callback_data=Nav(action="help").pack()),
        ],
    ]
    rows.append([btn("🕘 History", callback_data=Nav(action="history").pack())])
    if is_admin:
        rows.append([btn("🛠 Admin dashboard", callback_data=AdminCB(action="menu").pack(),
                         style="success")])
    rows.append([btn("⭐️ Releases", url=texts.RELEASES_URL, style="primary")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _quality_value(quality: Quality) -> str:
    if quality.kind.value == "audio":
        return str(quality.bitrate or 192)
    return "best" if quality.height is None else str(quality.height)


def quality_menu(ticket: int, *, default: Optional[Quality] = None) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    if default is not None:
        # One tap for returning users; "Reset" drops the stored preference.
        rows.append(
            [
                btn(
                    f"⚡ My usual · {default.label}",
                    style="success",
                    callback_data=QualityCB(
                        kind=default.kind.value,
                        value=_quality_value(default),
                        ticket=ticket,
                    ).pack(),
                ),
                btn(
                    "↺ Reset",
                    style="danger",
                    callback_data=QualityCB(kind="forget", value="-", ticket=ticket).pack(),
                ),
            ]
        )
    rows.append(
        [btn("⚡ Best quality", callback_data=QualityCB(kind="video", value="best",
                                                       ticket=ticket).pack(), style="success")]
    )
    for start in range(0, len(VIDEO_QUALITIES), 3):
        rows.append([
            btn(label, callback_data=QualityCB(kind="video", value=str(height),
                                               ticket=ticket).pack())
            for height, label in VIDEO_QUALITIES[start : start + 3]
        ])
    rows.append([
        btn(f"🎵 {bitrate} kbps", style="success" if bitrate == 320 else "primary",
            callback_data=QualityCB(kind="audio", value=str(bitrate), ticket=ticket).pack())
        for bitrate in AUDIO_QUALITIES[:3]
    ])
    rows.append([
        btn(f"🎵 {bitrate} kbps", callback_data=QualityCB(kind="audio", value=str(bitrate),
                                                          ticket=ticket).pack())
        for bitrate in AUDIO_QUALITIES[3:]
    ])
    rows.append([_back()])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def progress_menu(ticket: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [btn("🛑 Cancel download", style="danger",
                 callback_data=TicketCB(action="cancel", ticket=ticket).pack())],
            [_back("home")],
        ]
    )


def result_menu(ticket: int, *, media: bool = False) -> InlineKeyboardMarkup:
    rows = [
        [
            btn("🔁 Download again", style="success",
                callback_data=TicketCB(action="again", ticket=ticket).pack()),
        ]
    ]
    if media:
        rows[0].append(
            btn("🎚 Change quality", callback_data=TicketCB(action="quality", ticket=ticket).pack())
        )
    rows.append([_back("home"), _close()])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def error_menu(ticket: int, *, media: bool = True) -> InlineKeyboardMarkup:
    rows = [
        [
            btn("🔄 Try again", style="success",
                callback_data=TicketCB(action="again", ticket=ticket).pack()),
        ]
    ]
    if media:
        # Instant-lane tickets have no quality ladder, so the button is noise.
        rows[0].append(
            btn("🎚 Quality", callback_data=TicketCB(action="quality", ticket=ticket).pack())
        )
    rows.append([_back("home")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def cancel_menu(ticket: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [btn("🔄 Start over", style="success",
                 callback_data=TicketCB(action="again", ticket=ticket).pack())],
            [_back("home")],
        ]
    )


def help_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [btn("✨ Supported sites", callback_data=Nav(action="sites").pack(), style="success")],
            [_back("home")],
        ]
    )


def sites_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[_back()]])


def stats_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [btn("🔄 Refresh", style="success", callback_data=Nav(action="stats").pack())],
            [_back("home")],
        ]
    )


def admin_menu(policy: ForceJoinPolicy, *, has_channel: bool = True) -> InlineKeyboardMarkup:
    toggle_label = "🔓 Force join: ON" if policy.enabled else "🔒 Force join: OFF"
    rows = [
        [
            btn(toggle_label, style="danger" if policy.enabled else "success",
                callback_data=AdminCB(action="toggle").pack()),
        ],
        [
            btn(
                f"📢 Channel: {policy.channel or 'not set'}",
                callback_data=AdminCB(action="channel").pack(),
            )
        ],
        [
            btn("📣 Broadcast", callback_data=AdminCB(action="broadcast").pack()),
            btn("🧹 Clear join cache", callback_data=AdminCB(action="cache").pack()),
        ],
        [
            btn("📦 Queue", callback_data=AdminCB(action="queue").pack()),
            btn("⚠️ Errors", callback_data=AdminCB(action="errors").pack()),
        ],
        [btn("🔄 Refresh", callback_data=AdminCB(action="menu").pack(), style="success")],
        [_back("home")],
    ]
    if not has_channel:
        rows.insert(1, [btn("📢 Set a channel first", callback_data=AdminCB(action="channel").pack(),
                            style="danger")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def admin_back_menu() -> InlineKeyboardMarkup:
    """Sub-view footer so every admin page can get back to the dashboard."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [btn("🔄 Refresh", callback_data=AdminCB(action="refresh").pack(), style="success")],
            [btn("⬅️ Back", callback_data=AdminCB(action="menu").pack()), _close()],
        ]
    )


def join_menu(policy: ForceJoinPolicy, *, invite_url: Optional[str] = None) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    target = invite_url or policy.invite_url
    if target:
        rows.append([btn("📢 Join channel", url=target, style="success")])
    rows.append([btn("✅ I joined", callback_data=JoinCB(action="check").pack(), style="success")])
    rows.append([_back("home")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def channel_set_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[_back()]])


def history_menu(tickets: Sequence[DownloadTicket]) -> InlineKeyboardMarkup:
    """One replay button per past download, plus a way home."""
    rows: list[list[InlineKeyboardButton]] = [
        [
            btn(
                f"🔁 {texts.history_label(ticket)}",
                callback_data=TicketCB(action="again", ticket=ticket.id).pack(),
            )
        ]
        for ticket in tickets[:8]
    ]
    rows.append([_back("home")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def broadcast_confirm_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                btn("📣 Send to everyone", style="success",
                    callback_data=AdminCB(action="send").pack()),
                btn("✖️ Abort", style="danger", callback_data=AdminCB(action="abort").pack()),
            ],
            [_back("home")],
        ]
    )
