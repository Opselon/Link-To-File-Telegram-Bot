"""Keyboards. Every screen has a way back — enforced by the tests."""

from __future__ import annotations

from typing import Optional, Sequence

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from linktofile.domain.models import DownloadTicket, ForceJoinPolicy, Quality
from linktofile.infrastructure.telegram import texts
from linktofile.infrastructure.telegram.callbacks import (
    AdminCB,
    JoinCB,
    LangCB,
    Nav,
    QualityCB,
    TicketCB,
)
from linktofile.infrastructure.telegram.i18n import (
    LANGUAGE_NAMES,
    DEFAULT_LANGUAGE,
    normalize,
    t,
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


def _back(action: str = "back", *, lang: str = DEFAULT_LANGUAGE) -> InlineKeyboardButton:
    label = t("btn_home", lang) if action == "home" else t("btn_back", lang)
    return btn(label, callback_data=Nav(action=action).pack(), style="primary")


def _close(*, lang: str = DEFAULT_LANGUAGE) -> InlineKeyboardButton:
    return btn(t("btn_close", lang), callback_data=Nav(action="close").pack(), style="danger")


def main_menu(*, is_admin: bool = False, lang: str = DEFAULT_LANGUAGE) -> InlineKeyboardMarkup:
    rows = [
        [btn(t("btn_sites", lang), callback_data=Nav(action="sites").pack())],
        [
            btn(t("btn_stats", lang), callback_data=Nav(action="stats").pack()),
            btn(t("btn_help", lang), callback_data=Nav(action="help").pack()),
        ],
        [btn(t("btn_history", lang), callback_data=Nav(action="history").pack())],
    ]
    if is_admin:
        rows.append([btn(t("btn_admin", lang), callback_data=AdminCB(action="menu").pack(),
                         style="success")])
    rows.append([btn(t("btn_language", lang), callback_data=Nav(action="lang").pack())])
    rows.append([btn(t("btn_releases", lang), url=texts.RELEASES_URL, style="primary")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _quality_value(quality: Quality) -> str:
    if quality.kind.value == "audio":
        return str(quality.bitrate or 192)
    return "best" if quality.height is None else str(quality.height)


def quality_menu(
    ticket: int, *, default: Optional[Quality] = None, lang: str = DEFAULT_LANGUAGE
) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    if default is not None:
        # One tap for returning users; "Reset" drops the stored preference.
        rows.append(
            [
                btn(
                    t("btn_my_usual", lang, label=default.label),
                    style="success",
                    callback_data=QualityCB(
                        kind=default.kind.value,
                        value=_quality_value(default),
                        ticket=ticket,
                    ).pack(),
                ),
                btn(
                    t("btn_reset", lang),
                    style="danger",
                    callback_data=QualityCB(kind="forget", value="-", ticket=ticket).pack(),
                ),
            ]
        )
    rows.append(
        [btn(t("btn_best", lang), callback_data=QualityCB(kind="video", value="best",
                                                          ticket=ticket).pack(),
             style="success")]
    )
    for start in range(0, len(VIDEO_QUALITIES), 3):
        rows.append([
            btn(label, callback_data=QualityCB(kind="video", value=str(height),
                                               ticket=ticket).pack())
            for height, label in VIDEO_QUALITIES[start : start + 3]
        ])
    rows.append([
        btn(t("btn_audio", lang, bitrate=bitrate), style="success" if bitrate == 320 else "primary",
            callback_data=QualityCB(kind="audio", value=str(bitrate), ticket=ticket).pack())
        for bitrate in AUDIO_QUALITIES[:3]
    ])
    rows.append([
        btn(t("btn_audio", lang, bitrate=bitrate), callback_data=QualityCB(kind="audio",
                                                                          value=str(bitrate),
                                                                          ticket=ticket).pack())
        for bitrate in AUDIO_QUALITIES[3:]
    ])
    rows.append([_back(lang=lang)])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def progress_menu(ticket: int, *, lang: str = DEFAULT_LANGUAGE) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [btn(t("btn_cancel_download", lang), style="danger",
                 callback_data=TicketCB(action="cancel", ticket=ticket).pack())],
            [_back("home", lang=lang)],
        ]
    )


def result_menu(
    ticket: int, *, media: bool = False, lang: str = DEFAULT_LANGUAGE
) -> InlineKeyboardMarkup:
    rows = [
        [
            btn(t("btn_again", lang), style="success",
                callback_data=TicketCB(action="again", ticket=ticket).pack()),
        ]
    ]
    if media:
        rows[0].append(
            btn(t("btn_change_quality", lang),
                callback_data=TicketCB(action="quality", ticket=ticket).pack())
        )
    rows.append([_back("home", lang=lang), _close(lang=lang)])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def error_menu(
    ticket: int, *, media: bool = True, lang: str = DEFAULT_LANGUAGE
) -> InlineKeyboardMarkup:
    rows = [
        [
            btn(t("btn_retry", lang), style="success",
                callback_data=TicketCB(action="again", ticket=ticket).pack()),
        ]
    ]
    if media:
        # Instant-lane tickets have no quality ladder, so the button is noise.
        rows[0].append(
            btn(t("btn_quality", lang), callback_data=TicketCB(action="quality", ticket=ticket).pack())
        )
    rows.append([_back("home", lang=lang)])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def cancel_menu(ticket: int, *, lang: str = DEFAULT_LANGUAGE) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [btn(t("btn_start_over", lang), style="success",
                 callback_data=TicketCB(action="again", ticket=ticket).pack())],
            [_back("home", lang=lang)],
        ]
    )


def help_menu(*, lang: str = DEFAULT_LANGUAGE) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [btn(t("btn_supported", lang), callback_data=Nav(action="sites").pack(), style="success")],
            [_back("home", lang=lang)],
        ]
    )


def sites_menu(*, lang: str = DEFAULT_LANGUAGE) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[_back(lang=lang)]])


def stats_menu(*, lang: str = DEFAULT_LANGUAGE) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [btn(t("btn_refresh", lang), style="success", callback_data=Nav(action="stats").pack())],
            [_back("home", lang=lang)],
        ]
    )


def language_menu(*, lang: str = DEFAULT_LANGUAGE) -> InlineKeyboardMarkup:
    """The /lang screen: both languages, the current one highlighted."""
    current = normalize(lang)
    rows = [
        [
            btn(
                LANGUAGE_NAMES[code],
                style="success" if code == current else "primary",
                callback_data=LangCB(action=code).pack(),
            )
            for code in ("en", "fa")
        ]
    ]
    rows.append([_back("home", lang=lang), _close(lang=lang)])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def admin_menu(
    policy: ForceJoinPolicy, *, has_channel: bool = True, lang: str = DEFAULT_LANGUAGE
) -> InlineKeyboardMarkup:
    toggle_label = (
        t("btn_join_on", lang) if policy.enabled else t("btn_join_off", lang)
    )
    rows = [
        [
            btn(toggle_label, style="danger" if policy.enabled else "success",
                callback_data=AdminCB(action="toggle").pack()),
        ],
        [
            btn(
                t("btn_channel", lang, channel=policy.channel or t("channel_not_set", lang)),
                callback_data=AdminCB(action="channel").pack(),
            )
        ],
        [
            btn(t("btn_broadcast", lang), callback_data=AdminCB(action="broadcast").pack()),
            btn(t("btn_clear_cache", lang), callback_data=AdminCB(action="cache").pack()),
        ],
        [
            btn(t("btn_queue", lang), callback_data=AdminCB(action="queue").pack()),
            btn(t("btn_errors", lang), callback_data=AdminCB(action="errors").pack()),
        ],
        [btn(t("btn_refresh", lang), callback_data=AdminCB(action="menu").pack(), style="success")],
        [_back("home", lang=lang)],
    ]
    if not has_channel:
        rows.insert(1, [btn(t("btn_set_channel", lang), callback_data=AdminCB(action="channel").pack(),
                            style="danger")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def admin_back_menu(*, lang: str = DEFAULT_LANGUAGE) -> InlineKeyboardMarkup:
    """Sub-view footer so every admin page can get back to the dashboard."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [btn(t("btn_refresh", lang), callback_data=AdminCB(action="refresh").pack(),
                 style="success")],
            [btn(t("btn_back", lang), callback_data=AdminCB(action="menu").pack()),
             _close(lang=lang)],
        ]
    )


def join_menu(
    policy: ForceJoinPolicy,
    *,
    invite_url: Optional[str] = None,
    lang: str = DEFAULT_LANGUAGE,
) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    target = invite_url or policy.invite_url
    if target:
        rows.append([btn(t("btn_join_channel", lang), url=target, style="success")])
    rows.append([btn(t("btn_i_joined", lang), callback_data=JoinCB(action="check").pack(),
                     style="success")])
    rows.append([_back("home", lang=lang)])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def channel_set_menu(*, lang: str = DEFAULT_LANGUAGE) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[_back(lang=lang)]])


def history_menu(
    tickets: Sequence[DownloadTicket], *, lang: str = DEFAULT_LANGUAGE
) -> InlineKeyboardMarkup:
    """One replay button per past download, plus a way home.

    The ticket label is the filename (already escaped) and the 🔁 replay mark is
    language-neutral, so the row itself needs no catalog entry.
    """
    rows: list[list[InlineKeyboardButton]] = [
        [
            btn(
                f"🔁 {texts.history_label(ticket)}",
                callback_data=TicketCB(action="again", ticket=ticket.id).pack(),
            )
        ]
        for ticket in tickets[:8]
    ]
    rows.append([_back("home", lang=lang)])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def broadcast_confirm_menu(*, lang: str = DEFAULT_LANGUAGE) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                btn(t("btn_send_all", lang), style="success",
                    callback_data=AdminCB(action="send").pack()),
                btn(t("btn_abort", lang), style="danger", callback_data=AdminCB(action="abort").pack()),
            ],
            [_back("home", lang=lang)],
        ]
    )
