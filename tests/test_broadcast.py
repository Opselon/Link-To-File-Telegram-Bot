"""Admin broadcast: draft → preview → confirm → fan-out → summary."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest
from aiogram.exceptions import TelegramBadRequest, TelegramRetryAfter

from linktofile.application.queue import DownloadQueue
from linktofile.domain.models import ForceJoinPolicy
from linktofile.domain.policies import FormatPolicy
from linktofile.infrastructure.persistence.sqlite import (
    SqliteSettings,
    SqliteStats,
    SqliteTickets,
    SqliteUsers,
)
from linktofile.infrastructure.telegram import handlers, keyboards
from linktofile.infrastructure.telegram.callbacks import AdminCB

ADMIN = 1


class FakeBot:
    """Sends succeed except for blocked chats; one optional flood-control hit."""

    def __init__(self, *, blocked=(), retry_once=()) -> None:
        self.sent: list[tuple[int, str]] = []
        self.blocked = set(blocked)
        self.retry_once = set(retry_once)

    async def send_message(self, chat_id, text, **kwargs):
        if chat_id in self.retry_once:
            self.retry_once.discard(chat_id)
            raise TelegramRetryAfter(SimpleNamespace(chat_id=chat_id), "flood", 0)
        if chat_id in self.blocked:
            raise TelegramBadRequest(SimpleNamespace(chat_id=chat_id), "chat not found")
        self.sent.append((chat_id, text))
        return SimpleNamespace(message_id=len(self.sent))


class FakeState:
    def __init__(self) -> None:
        self.data: dict = {}
        self.state = None
        self.cleared = 0

    async def set_state(self, state) -> None:
        self.state = state

    async def update_data(self, **kwargs) -> None:
        self.data.update(kwargs)

    async def get_data(self) -> dict:
        return dict(self.data)

    async def clear(self) -> None:
        self.cleared += 1
        self.data.clear()
        self.state = None


class FakeMessage:
    def __init__(self, text: str = "", user_id: int = ADMIN) -> None:
        self.text = text
        self.chat = SimpleNamespace(id=user_id)
        self.from_user = SimpleNamespace(id=user_id, first_name="Root", is_bot=False)
        self.answers: list[str] = []
        self.markups: list = []

    async def answer(self, text, **kwargs):
        self.answers.append(text)
        self.markups.append(kwargs.get("reply_markup"))
        return self

    async def edit_text(self, text, **kwargs):
        self.answers.append(text)
        self.markups.append(kwargs.get("reply_markup"))
        return self


class FakeCallback:
    def __init__(self, message: FakeMessage, user_id: int = ADMIN) -> None:
        self.message = message
        self.from_user = SimpleNamespace(id=user_id, first_name="Root", is_bot=False)
        self.alerts: list[str] = []

    async def answer(self, text: str = "", show_alert: bool = False):
        if text:
            self.alerts.append(text)


@pytest.fixture
def services(database, tmp_path):
    settings_repo = SqliteSettings(database)
    users = SqliteUsers(database)
    for user_id in (1, 11, 12, 13):
        users.register(user_id)
    return SimpleNamespace(
        bot=FakeBot(blocked={12}),
        settings=SimpleNamespace(
            force_join=lambda: ForceJoinPolicy(),
            toggle_force_join=lambda: ForceJoinPolicy(),
        ),
        access=SimpleNamespace(forget_membership_cache=lambda: None),
        membership=None,
        tickets=SqliteTickets(database),
        users=users,
        stats=SqliteStats(database),
        settings_repo=settings_repo,
        queue=DownloadQueue(concurrency=1, max_size=5),
        workflow=SimpleNamespace(temp_dir=tmp_path),
        policy=FormatPolicy(cap_bytes=1024 * 1024),
        cap_mb=50,
        admin_id=ADMIN,
        version="3.1.0",
        is_admin=lambda user_id: user_id == ADMIN,
    )


def test_the_confirm_menu_can_abort_and_still_goes_home():
    markup = keyboards.broadcast_confirm_menu()
    payloads = [
        AdminCB.unpack(b.callback_data)
        for row in markup.inline_keyboard
        for b in row
        if (b.callback_data or "").startswith("adm:")
    ]
    assert {p.action for p in payloads} == {"send", "abort"}


async def test_the_draft_is_echoed_and_confirmed(services):
    message = FakeMessage("🚀 v3.1 is live!")
    state = FakeState()

    await handlers.on_broadcast_input(message, services, state)

    assert state.data["broadcast"] == "🚀 v3.1 is live!"
    assert message.answers[0] == "🚀 v3.1 is live!"  # the preview
    assert "4" in message.answers[-1]  # recipient count
    assert message.markups[-1] is not None


async def test_an_empty_draft_is_rejected(services):
    state = FakeState()
    message = FakeMessage("   ")

    await handlers.on_broadcast_input(message, services, state)

    assert "broadcast" not in state.data
    assert "1–4000" in message.answers[0]


async def test_a_non_admin_cannot_use_the_broadcast_flow(services):
    state = FakeState()
    message = FakeMessage("spam", user_id=99)

    await handlers.on_broadcast_input(message, services, state)

    assert state.cleared == 1
    assert message.answers == []


async def test_a_broadcast_reaches_every_user_and_reports_back(services):
    message = FakeMessage()
    state = FakeState()
    await handlers.on_broadcast_input(FakeMessage("hello"), services, state)

    await handlers.on_admin(FakeCallback(message), AdminCB(action="send"), services, state)
    await asyncio.sleep(0.4)

    delivered = {user_id for user_id, _ in services.bot.sent}
    assert delivered == {1, 11, 13}  # 12 blocked, admin gets the copy + summary
    summary = [text for user_id, text in services.bot.sent if "Broadcast finished" in text]
    assert summary and "Delivered: <b>3</b>" in summary[0]


async def test_flood_control_is_retried_instead_of_lost(services):
    services.bot = FakeBot(retry_once={11})
    state = FakeState()
    await handlers.on_broadcast_input(FakeMessage("retry me"), services, state)

    await handlers.on_admin(FakeCallback(FakeMessage()), AdminCB(action="send"), services, state)
    await asyncio.sleep(2.0)

    assert 11 in {user_id for user_id, _ in services.bot.sent}


async def test_aborting_clears_the_draft_and_sends_nothing(services):
    state = FakeState()
    await handlers.on_broadcast_input(FakeMessage("draft"), services, state)

    await handlers.on_admin(FakeCallback(FakeMessage()), AdminCB(action="abort"), services, state)
    await asyncio.sleep(0.1)

    assert state.data == {}
    assert services.bot.sent == []


async def test_send_without_a_draft_is_refused(services):
    callback = FakeCallback(FakeMessage())

    await handlers.on_admin(callback, AdminCB(action="send"), services, FakeState())

    assert any("Nothing to send" in alert for alert in callback.alerts)
