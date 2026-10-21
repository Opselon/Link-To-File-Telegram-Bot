"""History strings, keyboards and the handler that ties them together."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from linktofile.domain.models import DownloadStatus, DownloadTicket
from linktofile.infrastructure.telegram import handlers, keyboards, texts
from linktofile.infrastructure.telegram.callbacks import Nav, TicketCB


def ticket(**kwargs) -> DownloadTicket:
    base = dict(id=1, user_id=7, url="https://example.com/x.zip")
    base.update(kwargs)
    return DownloadTicket(**base)


def buttons(markup):
    return [button for row in markup.inline_keyboard for button in row]


# --- texts ------------------------------------------------------------------
def test_history_label_prefers_the_filename_then_the_url_name():
    assert texts.history_label(ticket(filename="a.zip")) == "a.zip"
    assert texts.history_label(ticket(url="https://cdn.example/deep/video.mp4?token=x")) == "video.mp4"
    assert texts.history_label(ticket(url="https://example.com")) == "example.com"


def test_history_label_is_escaped_and_truncated():
    escaped = texts.history_label(ticket(filename="<script>.zip"))
    assert "<script>" not in escaped
    assert "&lt;script&gt;.zip" in escaped

    long_name = "x" * 80 + ".zip"
    assert len(texts.history_label(ticket(filename=long_name))) <= 34


def test_history_text_renders_the_status_of_each_lines():
    items = [
        ticket(id=1, url="https://example.com/done.zip", status=DownloadStatus.COMPLETED,
               filename="done.zip", size=2048),
        ticket(id=2, url="https://example.com/broken.mp4", status=DownloadStatus.FAILED),
    ]
    rendered = texts.history_text(items)
    assert "done.zip · 2.0 KB" in rendered
    assert "⚠️" in rendered and "✅" in rendered
    assert texts.history_text([])  # empty state never crashes


# --- keyboards --------------------------------------------------------------
def test_history_menu_replays_the_user_tickets():
    items = [ticket(id=5, filename="one.zip"), ticket(id=6, filename="two.zip")]

    markup = keyboards.history_menu(items)
    again = [
        TicketCB.unpack(b.callback_data)
        for b in buttons(markup)
        if (b.callback_data or "").startswith("t:")
    ]

    assert [item.action for item in again] == ["again", "again"]
    assert [item.ticket for item in again] == [5, 6]


def test_history_menu_is_capped_at_eight_rows():
    items = [ticket(id=i, filename=f"f{i}.zip") for i in range(20)]
    assert len(keyboards.history_menu(items).inline_keyboard) == 9  # 8 + back


# --- handler ----------------------------------------------------------------
class FakeMessage:
    def __init__(self, text: str = "") -> None:
        self.text = text
        self.chat = SimpleNamespace(id=7)
        self.from_user = SimpleNamespace(id=7, first_name="Ada", is_bot=False)
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


class FakeAccess:
    async def allows(self, user_id: int) -> bool:
        return True


class FakeCallback:
    def __init__(self, message: FakeMessage, user_id: int = 7) -> None:
        self.message = message
        self.from_user = SimpleNamespace(id=user_id, first_name="Ada", is_bot=False)
        self.alerts: list[str] = []

    async def answer(self, text: str = "", show_alert: bool = False):
        if text:
            self.alerts.append(text)


@pytest.fixture
def handler(database):
    from linktofile.infrastructure.persistence.sqlite import SqliteTickets, SqliteUsers

    services = SimpleNamespace(
        tickets=SqliteTickets(database),
        users=SqliteUsers(database),
        access=FakeAccess(),
        cap_mb=50,
        is_admin=lambda user_id: user_id == 1,
    )
    services.users.register(7)
    services.users.register(99)
    services.tickets.create(7, "https://example.com/one.zip")
    services.tickets.create(99, "https://example.com/other.zip")
    return services


async def test_history_is_scoped_to_the_calling_user(handler):
    message = FakeMessage("/history")

    await handlers.cmd_history(message, handler)

    rendered = message.answers[0]
    assert "one.zip" in rendered
    assert "other.zip" not in rendered
    again = [
        TicketCB.unpack(b.callback_data)
        for b in buttons(message.markups[0])
        if (b.callback_data or "").startswith("t:")
    ]
    assert [item.ticket for item in again] == [1]


async def test_the_history_view_is_reachable_from_the_menu(handler):
    message = FakeMessage()

    await handlers.on_nav(FakeCallback(message), Nav(action="history"), handler, _FakeState())

    rendered = message.answers[-1]
    assert "one.zip" in rendered
    assert again_buttons(message.markups[-1]), "history screen must offer replay buttons"


def again_buttons(markup):
    return [
        TicketCB.unpack(b.callback_data)
        for b in buttons(markup)
        if (b.callback_data or "").startswith("t:")
    ]


class _FakeState:
    async def clear(self):
        return None


async def test_empty_history_gets_a_friendly_card(handler):
    handler.tickets = SimpleNamespace(recent=lambda user_id, limit=8: [])
    message = FakeMessage()

    await handlers.cmd_history(message, handler)

    assert "Nothing here yet" in message.answers[0]
