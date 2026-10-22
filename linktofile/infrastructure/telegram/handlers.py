"""Telegram handlers. Thin: parse the update, call a use case, render."""

from __future__ import annotations

import asyncio
import html
import logging
import shutil
from typing import Optional

from aiogram import F, Router
from aiogram.exceptions import TelegramBadRequest, TelegramRetryAfter
from aiogram.filters import Command, CommandStart, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

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
    Nav,
    QualityCB,
    TicketCB,
)
from linktofile.infrastructure.telegram.delivery import TelegramDelivery
from linktofile.infrastructure.telegram.states import AdminFlow
from linktofile.shared.security import ensure_public_url
from linktofile.shared.telemetry import pipeline
from linktofile.shared.utils import format_size, is_media_url, is_safe_url, is_valid_url

logger = logging.getLogger(__name__)
router = Router(name="core")

# Pause between broadcast sends: ~20 messages/s stays under Telegram's 30/s cap.
_BROADCAST_SPACING = 0.05


# --- helpers ----------------------------------------------------------------
def _header(quality: Optional[Quality]) -> str:
    if quality is None or quality.kind is QualityKind.VIDEO and quality.height is None:
        return "🔗 <b>Fetching your file</b>"
    if quality.kind is QualityKind.AUDIO:
        return f"🎵 <b>Audio · {quality.label}</b>"
    return f"🎬 <b>Video · {quality.label}</b>"


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


async def _show_menu(message: Message, services: Services, state: Optional[FSMContext]) -> None:
    if state is not None:
        await state.clear()
    is_admin = services.is_admin(message.chat.id)
    await message.answer(
        texts.welcome(message.from_user, is_admin=is_admin, cap_mb=services.cap_mb),
        parse_mode="HTML",
        reply_markup=keyboards.main_menu(is_admin=is_admin),
        disable_web_page_preview=True,
    )


async def _join_wall(message: Message, services: Services) -> None:
    policy = services.settings.force_join()
    target = policy.invite_url
    await message.answer(
        texts.join_text(policy),
        parse_mode="HTML",
        reply_markup=keyboards.join_menu(policy, invite_url=target or None),
        disable_web_page_preview=True,
    )


async def _enforce_gate(message: Message, services: Services) -> bool:
    """Returns True when the user must be stopped here (join wall shown)."""
    if await services.access.allows(message.from_user.id):
        return False
    await _join_wall(message, services)
    return True


async def _show_quality_card(
    message: Message,
    ticket: DownloadTicket,
    *,
    default: Optional[Quality],
    info: Optional[MediaInfo],
) -> None:
    """Preview card when metadata is available, the plain picker otherwise."""
    caption = texts.preview_caption(info) if info is not None else "🎚 <b>Pick a quality</b>"
    markup = keyboards.quality_menu(ticket.id, default=default)
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
    header: Optional[str] = None,
    note: Optional[str] = None,
) -> bool:
    header = header or _header(quality)
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
            texts.duplicate_text(existing.id if existing else ticket.id),
            parse_mode="HTML",
            reply_markup=keyboards.progress_menu(existing.id if existing else ticket.id),
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
            texts.too_many_jobs_text() if services.queue.load(ticket.user_id)
            else texts.queue_full_text(),
            parse_mode="HTML",
        )
        return False

    card = texts.queued_text(header, position)
    if note:
        card += f"\n\n{note}"
    await message.answer(
        card,
        parse_mode="HTML",
        reply_markup=keyboards.progress_menu(ticket.id),
    )
    return True


# --- commands ---------------------------------------------------------------
@router.message(CommandStart())
async def cmd_start(message: Message, services: Services, state: FSMContext) -> None:
    await state.clear()
    services.users.register(message.from_user.id)
    await _show_menu(message, services, None)


@router.message(Command("help"))
async def cmd_help(message: Message, services: Services) -> None:
    if await _enforce_gate(message, services):
        return
    await message.answer(
        texts.help_text(services.cap_mb), parse_mode="HTML", reply_markup=keyboards.help_menu()
    )


@router.message(Command("stats"))
async def cmd_stats(message: Message, services: Services) -> None:
    if await _enforce_gate(message, services):
        return
    stats = services.stats.user(message.from_user.id)
    await message.answer(
        texts.stats_text(stats), parse_mode="HTML", reply_markup=keyboards.stats_menu()
    )


@router.message(Command("history"))
async def cmd_history(message: Message, services: Services) -> None:
    if await _enforce_gate(message, services):
        return
    items = services.tickets.recent(message.from_user.id, limit=8)
    await message.answer(
        texts.history_text(items),
        parse_mode="HTML",
        reply_markup=keyboards.history_menu(items),
        disable_web_page_preview=True,
    )


@router.message(Command("id"))
async def cmd_id(message: Message) -> None:
    await message.answer(f"🆔 <code>{message.from_user.id}</code>", parse_mode="HTML")


@router.message(Command("version"))
async def cmd_version(message: Message, services: Services) -> None:
    await message.answer(f"⚡ <b>{texts.BRAND}</b> v{services.version}", parse_mode="HTML")


@router.message(Command("cancel"))
async def cmd_cancel(message: Message, services: Services) -> None:
    live = services.active.of_user(message.from_user.id)
    if not live:
        await message.answer("Nothing is running right now. 🌤", parse_mode="HTML")
        return
    for ticket_id in live:
        services.queue.cancel(ticket_id)
        services.tickets.cancel(ticket_id)
        event = services.active.event(ticket_id)
        if event:
            event.set()
    await message.answer(texts.cancel_text(), parse_mode="HTML")


# --- links ------------------------------------------------------------------
# StateFilter(None): a plain link must never be swallowed by an open admin flow
# (channel input, broadcast draft). FSM handlers match their state first.
@router.message(StateFilter(None), F.text)
async def on_text(message: Message, services: Services) -> None:
    text = (message.text or "").strip()
    user_id = message.from_user.id

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
        await message.answer("🤔 That doesn't look like a link. Try again?", parse_mode="HTML")
        return
    if not is_safe_url(url):
        pipeline("link.rejected", user=user_id, host=host, reason="unsafe")
        await message.answer(texts.blocked_url_text(), parse_mode="HTML")
        return
    # Second SSRF layer: a public-looking hostname can still resolve to
    # 127.0.0.1 or a cloud metadata address. This also rejects odd numeric
    # encodings that the static check cannot normalise.
    try:
        await ensure_public_url(url)
    except (UnsafeUrl, InvalidUrl) as exc:
        pipeline("link.rejected", user=user_id, host=host, reason="ssrf", detail=str(exc)[:120])
        await message.answer(texts.blocked_url_text(), parse_mode="HTML")
        return
    except Exception as exc:  # noqa: BLE001 - a resolver hiccup must not 500 the user
        logger.debug("SSRF resolution check skipped for %s: %s", host, exc)
    if not services.limiter.hit(user_id):
        pipeline("link.rejected", user=user_id, host=host, reason="rate_limited")
        await message.answer(
            texts.rate_limited_text(services.limiter.remaining(user_id)), parse_mode="HTML"
        )
        return
    if await _enforce_gate(message, services):
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
            header=texts.instant_header(host),
            note=texts.instant_note(host),
        )
        return

    if is_media_url(url):
        default = services.prefs.get(user_id)
        info = await services.preview.describe(url) if is_media_site(url) else None
        pipeline("route.picker", ticket=ticket.id, host=host, preview=info is not None)
        await _show_quality_card(message, ticket, default=default, info=info)
        return

    pipeline("route.direct", ticket=ticket.id, host=host)
    await _enqueue(services, ticket, Quality.video(None), message, media=False)


# --- quality ----------------------------------------------------------------
@router.callback_query(QualityCB.filter())
async def on_quality(
    callback: CallbackQuery, callback_data: QualityCB, services: Services
) -> None:
    if callback_data.kind == "forget":
        services.prefs.clear(callback.from_user.id)
        if callback.message is not None:
            markup = keyboards.quality_menu(callback_data.ticket)
            # The picker may live on a photo card: editing "text" there fails
            # on Telegram, so the caption is the thing to rewrite.
            try:
                if getattr(callback.message, "photo", None):
                    await callback.message.edit_caption(
                        caption="🎚 <b>Pick a quality</b>",
                        parse_mode="HTML",
                        reply_markup=markup,
                    )
                else:
                    await callback.message.edit_text(
                        "🎚 <b>Pick a quality</b>",
                        parse_mode="HTML",
                        reply_markup=markup,
                    )
            except TelegramBadRequest as exc:
                logger.debug("Could not refresh the picker after reset: %s", exc)
        await callback.answer("Preference cleared ↺")
        return

    quality = _quality(callback_data.kind, callback_data.value)
    if quality is None:
        await callback.answer("Bad selection.", show_alert=True)
        return
    ticket = _own_ticket(services, callback_data.ticket, callback.from_user.id)
    if ticket is None:
        await callback.answer(texts.missing_download_text(), show_alert=True)
        return
    if callback.message is None:
        await callback.answer()
        return
    if await _enforce_gate(callback.message, services):
        await callback.answer("Join the channel first ⚠️", show_alert=True)
        return

    media = is_media_url(ticket.url) or quality.kind is QualityKind.AUDIO
    ok = await _enqueue(services, ticket, quality, callback.message, media=media)
    if ok:
        services.prefs.set(callback.from_user.id, quality)
        await callback.answer("Added to the queue ⚡")


# --- ticket actions ---------------------------------------------------------
@router.callback_query(TicketCB.filter())
async def on_ticket(
    callback: CallbackQuery, callback_data: TicketCB, services: Services
) -> None:
    if callback.message is None:
        await callback.answer()
        return

    if callback_data.action == "close":
        await callback.message.delete()
        await callback.answer()
        return

    ticket = _own_ticket(services, callback_data.ticket, callback.from_user.id)
    if ticket is None:
        await callback.answer(texts.missing_download_text(), show_alert=True)
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
        await callback.answer("🛑 Cancelled" if stopped else "Nothing left to stop")
        await callback.message.edit_text(
            texts.cancel_text(), parse_mode="HTML", reply_markup=keyboards.cancel_menu(ticket.id)
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
                header=texts.instant_header(url_host(ticket.url)),
            )
            await callback.answer("⚡ Auto quality — fetching again")
            return
        await callback.message.edit_text(
            "🎚 <b>Pick another quality</b>",
            parse_mode="HTML",
            reply_markup=keyboards.quality_menu(
                ticket.id, default=services.prefs.get(callback.from_user.id)
            ),
        )
        await callback.answer()
        return

    # again
    if await _enforce_gate(callback.message, services):
        await callback.answer("Join the channel first ⚠️", show_alert=True)
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
            header=texts.instant_header(url_host(ticket.url)),
        )
        await callback.answer()
        return
    if is_media_url(ticket.url):
        await callback.message.edit_text(
            "🎚 <b>Pick a quality</b>",
            parse_mode="HTML",
            reply_markup=keyboards.quality_menu(
                fresh.id, default=services.prefs.get(callback.from_user.id)
            ),
        )
        await callback.answer()
        return
    await _enqueue(services, fresh, Quality.video(None), callback.message, media=False)
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

    if action == "sites":
        text, markup = texts.sites_text(), keyboards.sites_menu()
    elif action == "help":
        text, markup = texts.help_text(services.cap_mb), keyboards.help_menu()
    elif action == "stats":
        text, markup = (
            texts.stats_text(services.stats.user(callback.from_user.id)),
            keyboards.stats_menu(),
        )
    elif action == "history":
        items = services.tickets.recent(callback.from_user.id, limit=8)
        text, markup = texts.history_text(items), keyboards.history_menu(items)
    else:  # home / back
        text = texts.welcome(
            callback.from_user,
            is_admin=services.is_admin(callback.from_user.id),
            cap_mb=services.cap_mb,
        )
        markup = keyboards.main_menu(is_admin=services.is_admin(callback.from_user.id))

    try:
        await callback.message.edit_text(
            text, parse_mode="HTML", reply_markup=markup, disable_web_page_preview=True
        )
    except Exception:  # noqa: BLE001 - identical content is fine
        pass
    await callback.answer()


# --- admin dashboard --------------------------------------------------------
def _admin_view(services: Services) -> str:
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
    )


def _admin_queue_view(services: Services) -> str:
    tickets = services.tickets.queue_contents()
    live = {ticket_id: phase for ticket_id, phase in services.queue.live_phases().items()}
    return texts.admin_queue_text(tickets, live=live)


def _admin_errors_view(services: Services) -> str:
    return texts.admin_errors_text(services.tickets.recent_failures())


@router.message(Command("admin"))
async def cmd_admin(message: Message, services: Services, state: FSMContext) -> None:
    if not services.is_admin(message.from_user.id):
        await message.answer("🚫 Admins only.")
        return
    await state.clear()
    policy = services.settings.force_join()
    await message.answer(
        _admin_view(services),
        parse_mode="HTML",
        reply_markup=keyboards.admin_menu(policy, has_channel=bool(policy.channel)),
    )


@router.callback_query(AdminCB.filter())
async def on_admin(
    callback: CallbackQuery, callback_data: AdminCB, services: Services, state: FSMContext
) -> None:
    if not services.is_admin(callback.from_user.id):
        await callback.answer("Admins only.", show_alert=True)
        return
    if callback.message is None:
        await callback.answer()
        return

    action = callback_data.action
    if action == "queue":
        await callback.message.edit_text(
            _admin_queue_view(services),
            parse_mode="HTML",
            reply_markup=keyboards.admin_back_menu(),
        )
        await callback.answer()
        return
    if action == "errors":
        await callback.message.edit_text(
            _admin_errors_view(services),
            parse_mode="HTML",
            reply_markup=keyboards.admin_back_menu(),
        )
        await callback.answer()
        return
    if action == "toggle":
        policy = services.settings.toggle_force_join()
        if policy.enabled and not policy.channel:
            await callback.answer(
                "Gate armed, but no channel set — add one next.", show_alert=True
            )
        else:
            await callback.answer("Force join is now " + ("on ✅" if policy.enabled else "off"))
    elif action == "cache":
        services.access.forget_membership_cache()
        await callback.answer("Membership cache cleared 🧹")
    elif action == "channel":
        await state.set_state(AdminFlow.waiting_channel)
        await callback.message.edit_text(
            "📢 <b>Send the channel</b>\n\n"
            "Use <code>@username</code> or a <code>-100…</code> id. The bot must be an "
            "admin there so it can verify members.",
            parse_mode="HTML",
            reply_markup=keyboards.channel_set_menu(),
        )
        await callback.answer()
        return
    elif action == "broadcast":
        await state.set_state(AdminFlow.waiting_broadcast)
        await callback.message.edit_text(
            texts.broadcast_prompt_text(),
            parse_mode="HTML",
            reply_markup=keyboards.channel_set_menu(),
        )
        await callback.answer()
        return
    elif action in {"send", "abort"}:
        data = await state.get_data()
        await state.clear()
        if action == "abort":
            await callback.answer("Broadcast aborted")
        else:
            draft = str(data.get("broadcast") or "").strip()
            if not draft:
                await callback.answer(
                    "Nothing to send — the draft expired. Try again.", show_alert=True
                )
            else:
                asyncio.create_task(_broadcast(services, callback.from_user.id, draft),
                                    name="broadcast")
                await callback.answer("Broadcast started 📣")

    # "refresh" and unknown actions fall through to a dashboard re-render below.
    policy = services.settings.force_join()
    await callback.message.edit_text(
        _admin_view(services),
        parse_mode="HTML",
        reply_markup=keyboards.admin_menu(policy, has_channel=bool(policy.channel)),
    )
    await callback.answer()


@router.message(AdminFlow.waiting_channel, F.text)
async def on_channel_input(message: Message, services: Services, state: FSMContext) -> None:
    if not services.is_admin(message.from_user.id):
        await state.clear()
        return
    raw = (message.text or "").strip()
    channel = raw if raw.startswith(("@", "-100")) else f"@{raw}"
    chat = await services.membership.resolve_channel(channel)
    if chat is None:
        await message.answer(
            "❌ I couldn't read that channel. Make sure the bot is an admin there, then "
            "send it again.",
            parse_mode="HTML",
            reply_markup=keyboards.channel_set_menu(),
        )
        return

    invite = chat.invite_link or (f"https://t.me/{chat.username}" if chat.username else "")
    policy = services.settings.update_force_join(channel=channel, invite_url=invite or "")
    services.access.forget_membership_cache()
    await state.clear()
    await message.answer(
        f"✅ Channel saved: <b>{html.escape(policy.channel)}</b>"
        + ("" if policy.enabled else "\nForce join is still off — toggle it when ready."),
        parse_mode="HTML",
        reply_markup=keyboards.admin_menu(policy, has_channel=True),
    )


@router.message(AdminFlow.waiting_broadcast, F.text)
async def on_broadcast_input(message: Message, services: Services, state: FSMContext) -> None:
    """The admin's draft: validate it, show exactly what users will get."""
    if not services.is_admin(message.from_user.id):
        await state.clear()
        return
    text = (message.text or "").strip()
    if not text or len(text) > 4000:
        await message.answer(
            "⚠️ Broadcast messages must be 1–4000 characters.\nSend the message again.",
            parse_mode="HTML",
            reply_markup=keyboards.channel_set_menu(),
        )
        return

    try:
        # This doubles as the HTML check: if it can't render for the admin it
        # would fail for every user too.
        await message.answer(text, parse_mode="HTML", disable_web_page_preview=True)
    except TelegramBadRequest as exc:
        await state.clear()
        await message.answer(
            f"⚠️ That message has invalid HTML:\n{html.escape(str(exc)[:200])}"
        )
        return

    await state.update_data(broadcast=text)
    await message.answer(
        texts.broadcast_confirm_text(services.users.count()),
        parse_mode="HTML",
        reply_markup=keyboards.broadcast_confirm_menu(),
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
        await services.bot.send_message(
            admin_id,
            texts.broadcast_summary_text(len(recipients), sent, failed),
            parse_mode="HTML",
        )
    except Exception:  # noqa: BLE001 - the report must never crash the task
        logger.warning("Broadcast summary could not be delivered to the admin")


# --- join gate --------------------------------------------------------------
@router.callback_query(JoinCB.filter())
async def on_join(callback: CallbackQuery, services: Services) -> None:
    if callback.message is None:
        await callback.answer()
        return
    if await services.access.allows(callback.from_user.id):
        await _show_menu(callback.message, services, None)
        await callback.answer("Welcome aboard ⚡")
        return
    await callback.answer("Not yet — join the channel first.", show_alert=True)
    await _join_wall(callback.message, services)
