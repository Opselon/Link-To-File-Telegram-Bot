"""Telegram handlers. Thin: parse the update, call a use case, render."""

from __future__ import annotations

import html
import logging
import shutil
from typing import Optional

from aiogram import F, Router
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from linktofile.application.container import Services
from linktofile.application.downloads import DownloadJob
from linktofile.domain.errors import QueueSaturated
from linktofile.domain.models import DownloadTicket, Quality, QualityKind
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
from linktofile.shared.utils import is_media_url, is_safe_url, is_valid_url

logger = logging.getLogger(__name__)
router = Router(name="core")


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


async def _enqueue(
    services: Services,
    ticket: DownloadTicket,
    quality: Quality,
    message: Message,
    *,
    media: bool,
) -> bool:
    header = _header(quality)
    delivery = TelegramDelivery(
        services.bot, message, ticket_id=ticket.id, header=header, media=media
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
    except QueueSaturated:
        services.active.close(ticket.id)
        await message.answer(
            texts.too_many_jobs_text() if services.queue.load(ticket.user_id)
            else texts.queue_full_text(),
            parse_mode="HTML",
        )
        return False

    await message.answer(
        texts.queued_text(header, position),
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
        event = services.active.event(ticket_id)
        if event:
            event.set()
    await message.answer(texts.cancel_text(), parse_mode="HTML")


# --- links ------------------------------------------------------------------
@router.message(F.text)
async def on_text(message: Message, services: Services) -> None:
    text = (message.text or "").strip()
    user_id = message.from_user.id

    if "http" not in text:
        await _show_menu(message, services, None)
        return

    url = text.split()[0]
    if not is_valid_url(url):
        await message.answer("🤔 That doesn't look like a link. Try again?", parse_mode="HTML")
        return
    if not is_safe_url(url):
        await message.answer(texts.blocked_url_text(), parse_mode="HTML")
        return
    if not services.limiter.hit(user_id):
        await message.answer(
            texts.rate_limited_text(services.limiter.remaining(user_id)), parse_mode="HTML"
        )
        return
    if await _enforce_gate(message, services):
        return

    services.users.register(user_id)
    ticket = services.tickets.create(user_id, url)

    if is_media_url(url):
        await message.answer(
            "🎚 <b>Pick a quality</b>",
            parse_mode="HTML",
            reply_markup=keyboards.quality_menu(ticket.id),
        )
        return

    await _enqueue(services, ticket, Quality.video(None), message, media=False)


# --- quality ----------------------------------------------------------------
@router.callback_query(QualityCB.filter())
async def on_quality(
    callback: CallbackQuery, callback_data: QualityCB, services: Services
) -> None:
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
        stopped = services.queue.cancel(ticket.id)
        event = services.active.event(ticket.id)
        if event:
            event.set()
        await callback.answer("🛑 Cancelled" if stopped else "Nothing left to stop")
        await callback.message.edit_text(
            texts.cancel_text(), parse_mode="HTML", reply_markup=keyboards.cancel_menu(ticket.id)
        )
        return

    if callback_data.action == "quality":
        await callback.message.edit_text(
            "🎚 <b>Pick another quality</b>",
            parse_mode="HTML",
            reply_markup=keyboards.quality_menu(ticket.id),
        )
        await callback.answer()
        return

    # again
    if await _enforce_gate(callback.message, services):
        await callback.answer("Join the channel first ⚠️", show_alert=True)
        return
    fresh = services.tickets.create(ticket.user_id, ticket.url)
    if is_media_url(ticket.url):
        await callback.message.edit_text(
            "🎚 <b>Pick a quality</b>",
            parse_mode="HTML",
            reply_markup=keyboards.quality_menu(fresh.id),
        )
        await callback.answer()
        return
    await _enqueue(services, fresh, Quality.video(None), callback.message, media=False)
    await callback.answer()


# --- navigation -------------------------------------------------------------
@router.callback_query(Nav.filter())
async def on_nav(callback: CallbackQuery, callback_data: Nav, services: Services) -> None:
    if callback.message is None:
        await callback.answer()
        return
    action = callback_data.action
    if action == "close":
        await callback.message.delete()
        await callback.answer()
        return

    if action == "sites":
        text, markup = texts.sites_text(), keyboards.sites_menu()
    elif action == "help":
        text, markup = texts.help_text(services.cap_mb), keyboards.help_menu()
    elif action == "stats":
        text, markup = (
            texts.stats_text(services.stats.user(callback.from_user.id)),
            keyboards.stats_menu(),
        )
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
    return texts.admin_text(
        services.settings.force_join(),
        stats,
        active=snapshot.active,
        pending=snapshot.pending,
        disk_free=f"{free // (1024 * 1024)} MB",
        version=services.version,
    )


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
