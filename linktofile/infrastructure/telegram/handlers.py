"""Telegram handlers. Thin: parse the update, call a use case, render."""

from __future__ import annotations

import asyncio
import logging
import shutil
from typing import Optional, Tuple

from aiogram import F, Router
from aiogram.exceptions import TelegramBadRequest, TelegramRetryAfter
from aiogram.filters import Command, CommandStart, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, Message

from linktofile.application.container import Services
from linktofile.application.downloads import DownloadJob
from linktofile.domain.errors import (
    DuplicateJob,
    InvalidUrl,
    QueueSaturated,
    UnsafeUrl,
)
from linktofile.domain.models import (
    DownloadTicket,
    FailureKind,
    MediaInfo,
    Quality,
    QualityKind,
)
from linktofile.infrastructure.download.engine import is_instant_url, is_media_site, url_host
from linktofile.infrastructure.system.metrics import directory_bytes, rss_mb, uptime_text
from linktofile.infrastructure.telegram import keyboards, texts
from linktofile.infrastructure.telegram.callbacks import (
    AdminCB,
    JoinCB,
    LangCB,
    Nav,
    QualityCB,
    TicketCB,
)
from linktofile.infrastructure.telegram.delivery import TelegramDelivery
from linktofile.infrastructure.telegram.i18n import DEFAULT_LANGUAGE, normalize, t
from linktofile.infrastructure.telegram.states import AdminFlow
from linktofile.shared.security import ensure_public_url
from linktofile.shared.telemetry import pipeline
from linktofile.shared.utils import format_size, is_media_url, is_safe_url, is_valid_url

logger = logging.getLogger(__name__)
router = Router(name="core")

# Pause between broadcast sends: ~20 messages/s stays under Telegram's 30/s cap.
_BROADCAST_SPACING = 0.05


# --- helpers ----------------------------------------------------------------
def _lang(services: Services, user_id: int) -> str:
    """The user's interface language; a preference hiccup never breaks a reply."""
    try:
        return services.prefs.language(user_id)
    except (AttributeError, TypeError, ValueError):
        return DEFAULT_LANGUAGE


def _adopt_telegram_language(services: Services, user) -> None:
    """First contact: store Telegram's language hint, but never a later override."""
    try:
        if services.prefs.has_language(user.id):
            return
        hint = normalize(getattr(user, "language_code", None))
        if hint != DEFAULT_LANGUAGE:
            services.prefs.set_language(user.id, hint)
    except (AttributeError, TypeError, ValueError):
        return


def _quality(kind: str, value: str) -> Optional[Quality]:
    try:
        if kind == "audio":
            return Quality.audio(int(value))
        return Quality.video(None if value == "best" else int(value))
    except ValueError:
        return None


def _own_ticket(services: Services, ticket_id: int, user_id: int) -> Optional[DownloadTicket]:
    ticket = services.tickets.get(ticket_id)
    if ticket is None or ticket.user_id != user_id:
        return None
    return ticket


async def _show_menu(
    message: Message,
    services: Services,
    state: Optional[FSMContext],
    *,
    lang: Optional[str] = None,
) -> None:
    if state is not None:
        await state.clear()
    # On a callback the message's from_user is the bot, so callers that know the
    # real user (like the join gate) hand their language in.
    lang = lang if lang is not None else _lang(services, message.from_user.id)
    is_admin = services.is_admin(message.chat.id)
    await message.answer(
        texts.welcome(message.from_user, is_admin=is_admin, cap_mb=services.cap_mb, lang=lang),
        parse_mode="HTML",
        reply_markup=keyboards.main_menu(is_admin=is_admin, lang=lang),
        disable_web_page_preview=True,
    )


async def _join_wall(
    message: Message, services: Services, *, lang: Optional[str] = None
) -> None:
    lang = lang if lang is not None else _lang(services, message.from_user.id)
    policy = services.settings.force_join()
    target = policy.invite_url
    await message.answer(
        texts.join_text(policy, lang=lang),
        parse_mode="HTML",
        reply_markup=keyboards.join_menu(policy, invite_url=target or None, lang=lang),
        disable_web_page_preview=True,
    )


async def _enforce_gate(
    message: Message, services: Services, *, lang: Optional[str] = None
) -> bool:
    """Returns True when the user must be stopped here (join wall shown)."""
    if await services.access.allows(message.from_user.id):
        return False
    await _join_wall(message, services, lang=lang)
    return True


async def _show_quality_card(
    message: Message,
    ticket: DownloadTicket,
    *,
    default: Optional[Quality],
    info: Optional[MediaInfo],
    lang: str = DEFAULT_LANGUAGE,
) -> None:
    """Preview card when metadata is available, the plain picker otherwise."""
    caption = (
        texts.preview_caption(info, lang=lang)
        if info is not None
        else texts.pick_quality_text(lang=lang)
    )
    markup = keyboards.quality_menu(ticket.id, default=default, lang=lang)
    if info is not None and info.has_thumbnail:
        try:
            await message.answer_photo(
                info.thumbnail, caption=caption, parse_mode="HTML", reply_markup=markup
            )
            return
        except Exception as exc:  # noqa: BLE001 - a bad thumbnail is never fatal
            logger.debug("Preview photo failed for ticket %s: %s", ticket.id, exc)
    await message.answer(
        caption, parse_mode="HTML", reply_markup=markup, disable_web_page_preview=True
    )


async def _enqueue(
    services: Services,
    ticket: DownloadTicket,
    quality: Quality,
    message: Message,
    *,
    media: bool,
    lang: str = DEFAULT_LANGUAGE,
    header: Optional[str] = None,
    note: Optional[str] = None,
) -> bool:
    header = header or texts.quality_header(quality, lang=lang)
    # Durable first: the row becomes 'queued' with its quality committed before
    # the job exists in memory, so a crash here is recoverable, and the partial
    # unique index rejects a second live job for the same (user, url).
    try:
        services.tickets.enqueue(ticket.id, quality)
    except DuplicateJob:
        services.tickets.fail(
            ticket.id, kind=FailureKind.DUPLICATE, error="Duplicate link."
        )
        existing = services.tickets.active_duplicate(ticket.user_id, ticket.url)
        await message.answer(
            texts.duplicate_text(existing.id if existing else ticket.id, lang=lang),
            parse_mode="HTML",
            reply_markup=keyboards.progress_menu(
                existing.id if existing else ticket.id, lang=lang
            ),
        )
        pipeline("link.duplicate", ticket=ticket.id, user=ticket.user_id, host=url_host(ticket.url))
        return False

    delivery = TelegramDelivery(
        services.bot,
        message,
        ticket_id=ticket.id,
        header=header,
        media=media,
        admin_id=getattr(services, "admin_id", None),
        lang=lang,
    )
    event = services.active.open(ticket.user_id, ticket.id)
    job = DownloadJob(ticket_id=ticket.id, user_id=ticket.user_id, url=ticket.url, quality=quality)

    async def run() -> None:
        try:
            await services.workflow.run(
                job, delivery, on_progress=delivery.progress, cancel_event=event
            )
        finally:
            services.active.close(ticket.id)

    try:
        position = await services.queue.submit(run, key=ticket.id, user_id=ticket.user_id)
    except (QueueSaturated, DuplicateJob):
        services.active.close(ticket.id)
        services.tickets.fail(
            ticket.id, kind=FailureKind.QUEUE_FULL, error="Not enough queue capacity."
        )
        await message.answer(
            texts.too_many_jobs_text(lang=lang) if services.queue.load(ticket.user_id)
            else texts.queue_full_text(lang=lang),
            parse_mode="HTML",
        )
        return False

    card = texts.queued_text(header, position, lang=lang)
    if note:
        card += f"\n\n{note}"
    await message.answer(
        card,
        parse_mode="HTML",
        reply_markup=keyboards.progress_menu(ticket.id, lang=lang),
    )
    return True


# --- commands ---------------------------------------------------------------
@router.message(CommandStart())
async def cmd_start(message: Message, services: Services, state: FSMContext) -> None:
    await state.clear()
    services.users.register(message.from_user.id)
    _adopt_telegram_language(services, message.from_user)
    await _show_menu(message, services, None)


@router.message(Command("help"))
async def cmd_help(message: Message, services: Services) -> None:
    lang = _lang(services, message.from_user.id)
    if await _enforce_gate(message, services):
        return
    await message.answer(
        texts.help_text(services.cap_mb, lang=lang),
        parse_mode="HTML",
        reply_markup=keyboards.help_menu(lang=lang),
    )


@router.message(Command("stats"))
async def cmd_stats(message: Message, services: Services) -> None:
    lang = _lang(services, message.from_user.id)
    if await _enforce_gate(message, services):
        return
    stats = services.stats.user(message.from_user.id)
    await message.answer(
        texts.stats_text(stats, lang=lang),
        parse_mode="HTML",
        reply_markup=keyboards.stats_menu(lang=lang),
    )


@router.message(Command("history"))
async def cmd_history(message: Message, services: Services) -> None:
    lang = _lang(services, message.from_user.id)
    if await _enforce_gate(message, services):
        return
    items = services.tickets.recent(message.from_user.id, limit=8)
    await message.answer(
        texts.history_text(items, lang=lang),
        parse_mode="HTML",
        reply_markup=keyboards.history_menu(items, lang=lang),
        disable_web_page_preview=True,
    )


@router.message(Command("id"))
async def cmd_id(message: Message, services: Services) -> None:
    lang = _lang(services, message.from_user.id)
    await message.answer(
        texts.your_id_text(message.from_user.id, lang=lang), parse_mode="HTML"
    )


@router.message(Command("version"))
async def cmd_version(message: Message, services: Services) -> None:
    lang = _lang(services, message.from_user.id)
    await message.answer(texts.version_text(services.version, lang=lang), parse_mode="HTML")


@router.message(Command("lang"))
async def cmd_lang(message: Message, services: Services) -> None:
    lang = _lang(services, message.from_user.id)
    await message.answer(
        texts.language_text(lang=lang),
        parse_mode="HTML",
        reply_markup=keyboards.language_menu(lang=lang),
    )


@router.message(Command("cancel"))
async def cmd_cancel(message: Message, services: Services) -> None:
    lang = _lang(services, message.from_user.id)
    live = services.active.of_user(message.from_user.id)
    if not live:
        await message.answer(texts.nothing_running_text(lang=lang), parse_mode="HTML")
        return
    for ticket_id in live:
        services.queue.cancel(ticket_id)
        services.tickets.cancel(ticket_id)
        event = services.active.event(ticket_id)
        if event:
            event.set()
    await message.answer(texts.cancel_text(lang=lang), parse_mode="HTML")


# --- links ------------------------------------------------------------------
# StateFilter(None): a plain link must never be swallowed by an open admin flow
# (channel input, broadcast draft). FSM handlers match their state first.
@router.message(StateFilter(None), F.text)
async def on_text(message: Message, services: Services) -> None:
    text = (message.text or "").strip()
    user_id = message.from_user.id
    lang = _lang(services, user_id)

    if "http" not in text:
        await _show_menu(message, services, None)
        return

    url = text.split()[0]
    host = url_host(url)
    pipeline(
        "link.received",
        user=user_id,
        host=host,
        media=is_media_url(url),
        instant=is_instant_url(url),
    )
    if not is_valid_url(url):
        pipeline("link.rejected", user=user_id, host=host, reason="invalid")
        await message.answer(
            texts.not_a_link_text(lang=lang), parse_mode="HTML"
        )
        return
    if not is_safe_url(url):
        pipeline("link.rejected", user=user_id, host=host, reason="unsafe")
        await message.answer(texts.blocked_url_text(lang=lang), parse_mode="HTML")
        return
    # Second SSRF layer: a public-looking hostname can still resolve to
    # 127.0.0.1 or a cloud metadata address. This also rejects odd numeric
    # encodings that the static check cannot normalise.
    try:
        await ensure_public_url(url)
    except (UnsafeUrl, InvalidUrl) as exc:
        pipeline("link.rejected", user=user_id, host=host, reason="ssrf", detail=str(exc)[:120])
        await message.answer(texts.blocked_url_text(lang=lang), parse_mode="HTML")
        return
    except Exception as exc:  # noqa: BLE001 - a resolver hiccup must not 500 the user
        logger.debug("SSRF resolution check skipped for %s: %s", host, exc)
    if not services.limiter.hit(user_id):
        pipeline("link.rejected", user=user_id, host=host, reason="rate_limited")
        await message.answer(
            texts.rate_limited_text(services.limiter.remaining(user_id), lang=lang),
            parse_mode="HTML",
        )
        return
    if await _enforce_gate(message, services, lang=lang):
        pipeline("link.rejected", user=user_id, host=host, reason="force_join")
        return

    services.users.register(user_id)
    ticket = services.tickets.create(user_id, url)

    if is_instant_url(url):
        # Instagram and friends publish one file per post: asking "which
        # quality?" adds a round-trip and a preview probe before the very same
        # download. Fetch it now, at the best quality that fits the cap.
        pipeline("route.instant", ticket=ticket.id, host=host)
        await _enqueue(
            services,
            ticket,
            Quality.video(None),
            message,
            media=False,
            lang=lang,
            header=texts.instant_header(host, lang=lang),
            note=texts.instant_note(host, lang=lang),
        )
        return

    if is_media_url(url):
        default = services.prefs.get(user_id)
        info = await services.preview.describe(url) if is_media_site(url) else None
        pipeline("route.picker", ticket=ticket.id, host=host, preview=info is not None)
        await _show_quality_card(message, ticket, default=default, info=info, lang=lang)
        return

    pipeline("route.direct", ticket=ticket.id, host=host)
    await _enqueue(services, ticket, Quality.video(None), message, media=False, lang=lang)


# --- quality ----------------------------------------------------------------
@router.callback_query(QualityCB.filter())
async def on_quality(
    callback: CallbackQuery, callback_data: QualityCB, services: Services
) -> None:
    lang = _lang(services, callback.from_user.id)
    if callback_data.kind == "forget":
        services.prefs.clear(callback.from_user.id)
        if callback.message is not None:
            markup = keyboards.quality_menu(callback_data.ticket, lang=lang)
            # The picker may live on a photo card: editing "text" there fails
            # on Telegram, so the caption is the thing to rewrite.
            try:
                if getattr(callback.message, "photo", None):
                    await callback.message.edit_caption(
                        caption=texts.pick_quality_text(lang=lang),
                        parse_mode="HTML",
                        reply_markup=markup,
                    )
                else:
                    await callback.message.edit_text(
                        texts.pick_quality_text(lang=lang),
                        parse_mode="HTML",
                        reply_markup=markup,
                    )
            except TelegramBadRequest as exc:
                logger.debug("Could not refresh the picker after reset: %s", exc)
        await callback.answer(t("pref_cleared", lang))
        return

    quality = _quality(callback_data.kind, callback_data.value)
    if quality is None:
        await callback.answer(t("bad_selection", lang), show_alert=True)
        return
    ticket = _own_ticket(services, callback_data.ticket, callback.from_user.id)
    if ticket is None:
        await callback.answer(texts.missing_download_text(lang=lang), show_alert=True)
        return
    if callback.message is None:
        await callback.answer()
        return
    if await _enforce_gate(callback.message, services, lang=lang):
        await callback.answer(t("join_first", lang), show_alert=True)
        return

    media = is_media_url(ticket.url) or quality.kind is QualityKind.AUDIO
    ok = await _enqueue(services, ticket, quality, callback.message, media=media, lang=lang)
    if ok:
        services.prefs.set(callback.from_user.id, quality)
        await callback.answer(t("added_to_queue", lang))


# --- ticket actions ---------------------------------------------------------
@router.callback_query(TicketCB.filter())
async def on_ticket(
    callback: CallbackQuery, callback_data: TicketCB, services: Services
) -> None:
    lang = _lang(services, callback.from_user.id)
    if callback.message is None:
        await callback.answer()
        return

    if callback_data.action == "close":
        await callback.message.delete()
        await callback.answer()
        return

    ticket = _own_ticket(services, callback_data.ticket, callback.from_user.id)
    if ticket is None:
        await callback.answer(texts.missing_download_text(lang=lang), show_alert=True)
        return

    if callback_data.action == "cancel":
        # Stop the in-memory job *and* the durable row: a queued ticket that
        # only left memory would be resumed as a zombie on the next restart.
        in_memory = services.queue.cancel(ticket.id)
        in_db = services.tickets.cancel(ticket.id)
        event = services.active.event(ticket.id)
        if event:
            event.set()
        stopped = in_memory or in_db
        await callback.answer(t("stopped_toast", lang) if stopped else t("nothing_to_stop", lang))
        await callback.message.edit_text(
            texts.cancel_text(lang=lang),
            parse_mode="HTML",
            reply_markup=keyboards.cancel_menu(ticket.id, lang=lang),
        )
        return

    if callback_data.action == "quality":
        if is_instant_url(ticket.url):
            # One file per post: there is nothing to choose, so "change quality"
            # means "fetch it again".
            await _enqueue(
                services,
                ticket,
                Quality.video(None),
                callback.message,
                media=False,
                lang=lang,
                header=texts.instant_header(url_host(ticket.url), lang=lang),
            )
            await callback.answer(t("auto_quality_again", lang))
            return
        await callback.message.edit_text(
            texts.pick_quality_other_text(lang=lang),
            parse_mode="HTML",
            reply_markup=keyboards.quality_menu(
                ticket.id, default=services.prefs.get(callback.from_user.id), lang=lang
            ),
        )
        await callback.answer()
        return

    # again
    if await _enforce_gate(callback.message, services, lang=lang):
        await callback.answer(t("join_first", lang), show_alert=True)
        return
    fresh = services.tickets.create(ticket.user_id, ticket.url)
    if is_instant_url(ticket.url):
        pipeline("route.instant", ticket=fresh.id, host=url_host(ticket.url), via="retry")
        await _enqueue(
            services,
            fresh,
            Quality.video(None),
            callback.message,
            media=False,
            lang=lang,
            header=texts.instant_header(url_host(ticket.url), lang=lang),
        )
        await callback.answer()
        return
    if is_media_url(ticket.url):
        await callback.message.edit_text(
            texts.pick_quality_text(lang=lang),
            parse_mode="HTML",
            reply_markup=keyboards.quality_menu(
                fresh.id, default=services.prefs.get(callback.from_user.id), lang=lang
            ),
        )
        await callback.answer()
        return
    await _enqueue(services, fresh, Quality.video(None), callback.message, media=False, lang=lang)
    await callback.answer()


# --- navigation -------------------------------------------------------------
@router.callback_query(Nav.filter())
async def on_nav(
    callback: CallbackQuery, callback_data: Nav, services: Services, state: FSMContext
) -> None:
    if callback.message is None:
        await callback.answer()
        return
    action = callback_data.action
    if action == "close":
        await callback.message.delete()
        await callback.answer()
        return

    # Leaving the screen abandons any half-finished admin flow (channel, broadcast)
    # so the next plain-text message is never swallowed by a stale state.
    await state.clear()
    lang = _lang(services, callback.from_user.id)
    view: Tuple[str, InlineKeyboardMarkup]

    if action == "sites":
        view = texts.sites_text(lang=lang), keyboards.sites_menu(lang=lang)
    elif action == "help":
        view = texts.help_text(services.cap_mb, lang=lang), keyboards.help_menu(lang=lang)
    elif action == "stats":
        view = (
            texts.stats_text(services.stats.user(callback.from_user.id), lang=lang),
            keyboards.stats_menu(lang=lang),
        )
    elif action == "history":
        items = services.tickets.recent(callback.from_user.id, limit=8)
        view = (
            texts.history_text(items, lang=lang),
            keyboards.history_menu(items, lang=lang),
        )
    elif action == "lang":
        view = texts.language_text(lang=lang), keyboards.language_menu(lang=lang)
    else:  # home / back
        is_admin = services.is_admin(callback.from_user.id)
        view = (
            texts.welcome(
                callback.from_user,
                is_admin=is_admin,
                cap_mb=services.cap_mb,
                lang=lang,
            ),
            keyboards.main_menu(is_admin=is_admin, lang=lang),
        )

    text, markup = view
    try:
        await callback.message.edit_text(
            text, parse_mode="HTML", reply_markup=markup, disable_web_page_preview=True
        )
    except Exception:  # noqa: BLE001 - identical content is fine
        pass
    await callback.answer()


# --- language ---------------------------------------------------------------
@router.callback_query(LangCB.filter())
async def on_lang(
    callback: CallbackQuery, callback_data: LangCB, services: Services
) -> None:
    if callback.message is None:
        await callback.answer()
        return
    # The payload carries no user id, so a tap can only ever set the language of
    # the person who tapped it — a per-user preference by design.
    lang = services.prefs.set_language(callback.from_user.id, callback_data.action)
    try:
        await callback.message.edit_text(
            texts.language_text(lang=lang),
            parse_mode="HTML",
            reply_markup=keyboards.language_menu(lang=lang),
        )
    except Exception:  # noqa: BLE001 - identical content is fine
        pass
    await callback.answer(t("language_saved", lang))


# --- admin dashboard --------------------------------------------------------
def _admin_view(services: Services, *, lang: str = DEFAULT_LANGUAGE) -> str:
    stats = services.stats.global_()
    snapshot = services.queue.snapshot()
    free = shutil.disk_usage(services.workflow.temp_dir).free
    recovered = getattr(services, "recovered", 0)
    return texts.admin_text(
        services.settings.force_join(),
        stats,
        active=snapshot.active,
        pending=snapshot.pending,
        disk_free=f"{free // (1024 * 1024)} MB",
        version=services.version,
        memory_mb=rss_mb(),
        temp_used=format_size(directory_bytes(services.workflow.temp_dir)),
        uptime=uptime_text(),
        failures_today=int(stats.get("errors", 0) or 0),
        resumed=recovered,
        lang=lang,
    )


def _admin_queue_view(services: Services, *, lang: str = DEFAULT_LANGUAGE) -> str:
    tickets = services.tickets.queue_contents()
    live = {ticket_id: phase for ticket_id, phase in services.queue.live_phases().items()}
    return texts.admin_queue_text(tickets, live=live, lang=lang)


def _admin_errors_view(services: Services, *, lang: str = DEFAULT_LANGUAGE) -> str:
    return texts.admin_errors_text(services.tickets.recent_failures(), lang=lang)


@router.message(Command("admin"))
async def cmd_admin(message: Message, services: Services, state: FSMContext) -> None:
    lang = _lang(services, message.from_user.id)
    if not services.is_admin(message.from_user.id):
        await message.answer(texts.admin_only_text(lang=lang), parse_mode="HTML")
        return
    await state.clear()
    policy = services.settings.force_join()
    await message.answer(
        _admin_view(services, lang=lang),
        parse_mode="HTML",
        reply_markup=keyboards.admin_menu(
            policy, has_channel=bool(policy.channel), lang=lang
        ),
    )


@router.callback_query(AdminCB.filter())
async def on_admin(
    callback: CallbackQuery, callback_data: AdminCB, services: Services, state: FSMContext
) -> None:
    lang = _lang(services, callback.from_user.id)
    if not services.is_admin(callback.from_user.id):
        await callback.answer(t("admin_only_toast", lang), show_alert=True)
        return
    if callback.message is None:
        await callback.answer()
        return

    action = callback_data.action
    if action == "queue":
        await callback.message.edit_text(
            _admin_queue_view(services, lang=lang),
            parse_mode="HTML",
            reply_markup=keyboards.admin_back_menu(lang=lang),
        )
        await callback.answer()
        return
    if action == "errors":
        await callback.message.edit_text(
            _admin_errors_view(services, lang=lang),
            parse_mode="HTML",
            reply_markup=keyboards.admin_back_menu(lang=lang),
        )
        await callback.answer()
        return
    if action == "toggle":
        policy = services.settings.toggle_force_join()
        if policy.enabled and not policy.channel:
            await callback.answer(t("gate_no_channel", lang), show_alert=True)
        else:
            await callback.answer(
                t("force_join_on_toast", lang) if policy.enabled
                else t("force_join_off_toast", lang)
            )
    elif action == "cache":
        services.access.forget_membership_cache()
        await callback.answer(t("cache_cleared_toast", lang))
    elif action == "channel":
        await state.set_state(AdminFlow.waiting_channel)
        await callback.message.edit_text(
            texts.channel_prompt_text(lang=lang),
            parse_mode="HTML",
            reply_markup=keyboards.channel_set_menu(lang=lang),
        )
        await callback.answer()
        return
    elif action == "broadcast":
        await state.set_state(AdminFlow.waiting_broadcast)
        await callback.message.edit_text(
            texts.broadcast_prompt_text(lang=lang),
            parse_mode="HTML",
            reply_markup=keyboards.channel_set_menu(lang=lang),
        )
        await callback.answer()
        return
    elif action in {"send", "abort"}:
        data = await state.get_data()
        await state.clear()
        if action == "abort":
            await callback.answer(t("broadcast_aborted_toast", lang))
        else:
            draft = str(data.get("broadcast") or "").strip()
            if not draft:
                await callback.answer(t("no_draft_toast", lang), show_alert=True)
            else:
                asyncio.create_task(_broadcast(services, callback.from_user.id, draft),
                                    name="broadcast")
                await callback.answer(t("broadcast_started_toast", lang))

    # "refresh" and unknown actions fall through to a dashboard re-render below.
    policy = services.settings.force_join()
    await callback.message.edit_text(
        _admin_view(services, lang=lang),
        parse_mode="HTML",
        reply_markup=keyboards.admin_menu(
            policy, has_channel=bool(policy.channel), lang=lang
        ),
    )
    await callback.answer()


@router.message(AdminFlow.waiting_channel, F.text)
async def on_channel_input(message: Message, services: Services, state: FSMContext) -> None:
    lang = _lang(services, message.from_user.id)
    if not services.is_admin(message.from_user.id):
        await state.clear()
        return
    raw = (message.text or "").strip()
    channel = raw if raw.startswith(("@", "-100")) else f"@{raw}"
    chat = await services.membership.resolve_channel(channel)
    if chat is None:
        await message.answer(
            texts.channel_bad_text(lang=lang),
            parse_mode="HTML",
            reply_markup=keyboards.channel_set_menu(lang=lang),
        )
        return

    invite = chat.invite_link or (f"https://t.me/{chat.username}" if chat.username else "")
    policy = services.settings.update_force_join(channel=channel, invite_url=invite or "")
    services.access.forget_membership_cache()
    await state.clear()
    await message.answer(
        texts.channel_saved_text(policy.channel, lang=lang)
        + ("" if policy.enabled else texts.channel_saved_off_text(lang=lang)),
        parse_mode="HTML",
        reply_markup=keyboards.admin_menu(policy, has_channel=True, lang=lang),
    )


@router.message(AdminFlow.waiting_broadcast, F.text)
async def on_broadcast_input(message: Message, services: Services, state: FSMContext) -> None:
    """The admin's draft: validate it, show exactly what users will get."""
    lang = _lang(services, message.from_user.id)
    if not services.is_admin(message.from_user.id):
        await state.clear()
        return
    text = (message.text or "").strip()
    if not text or len(text) > 4000:
        await message.answer(
            texts.broadcast_too_long_text(lang=lang),
            parse_mode="HTML",
            reply_markup=keyboards.channel_set_menu(lang=lang),
        )
        return

    try:
        # This doubles as the HTML check: if it can't render for the admin it
        # would fail for every user too.
        await message.answer(text, parse_mode="HTML", disable_web_page_preview=True)
    except TelegramBadRequest as exc:
        await state.clear()
        await message.answer(
            texts.broadcast_bad_html_text(str(exc)[:200], lang=lang), parse_mode="HTML"
        )
        return

    await state.update_data(broadcast=text)
    await message.answer(
        texts.broadcast_confirm_text(services.users.count(), lang=lang),
        parse_mode="HTML",
        reply_markup=keyboards.broadcast_confirm_menu(lang=lang),
    )


async def _broadcast(services: Services, admin_id: int, text: str) -> None:
    """Fan ``text`` out to every known user; tolerant of limits and dead chats."""
    recipients = services.users.ids()
    sent = failed = 0
    for user_id in recipients:
        try:
            await services.bot.send_message(
                user_id, text, parse_mode="HTML", disable_web_page_preview=True
            )
            sent += 1
        except TelegramRetryAfter as exc:
            await asyncio.sleep(min(float(getattr(exc, "retry_after", 0)) or 1.0, 5.0))
            try:
                await services.bot.send_message(
                    user_id, text, parse_mode="HTML", disable_web_page_preview=True
                )
                sent += 1
            except Exception:  # noqa: BLE001 - still not deliverable
                failed += 1
        except Exception:  # noqa: BLE001 - blocked bot / deleted account: keep going
            failed += 1
        await asyncio.sleep(_BROADCAST_SPACING)

    logger.info("Broadcast finished: %s sent, %s failed, %s recipient(s)", sent, failed, len(recipients))
    try:
        # The report goes to the operator in the operator's own language.
        await services.bot.send_message(
            admin_id,
            texts.broadcast_summary_text(len(recipients), sent, failed,
                                         lang=_lang(services, admin_id)),
            parse_mode="HTML",
        )
    except Exception:  # noqa: BLE001 - the report must never crash the task
        logger.warning("Broadcast summary could not be delivered to the admin")


# --- join gate --------------------------------------------------------------
@router.callback_query(JoinCB.filter())
async def on_join(callback: CallbackQuery, services: Services) -> None:
    lang = _lang(services, callback.from_user.id)
    if callback.message is None:
        await callback.answer()
        return
    if await services.access.allows(callback.from_user.id):
        await _show_menu(callback.message, services, None, lang=lang)
        await callback.answer(t("welcome_aboard", lang))
        return
    await callback.answer(t("not_yet", lang), show_alert=True)
    await _join_wall(callback.message, services, lang=lang)
