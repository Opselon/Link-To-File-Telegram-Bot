"""Bilingual surface: catalog parity, fallbacks, storage and handler wiring."""

from __future__ import annotations

import re
from types import SimpleNamespace

import pytest

from linktofile.application.preferences import UserPreferences
from linktofile.domain.errors import MediaUnavailable
from linktofile.domain.models import MediaInfo, Quality
from linktofile.infrastructure.persistence.sqlite import SqliteSettings, SqliteUsers
from linktofile.infrastructure.telegram import handlers, keyboards, texts
from linktofile.infrastructure.telegram.callbacks import LangCB, Nav, QualityCB
from linktofile.infrastructure.telegram.delivery import TelegramDelivery
from linktofile.infrastructure.telegram.i18n import (
    CATALOG,
    DEFAULT_LANGUAGE,
    LANGUAGE_NAMES,
    LANGUAGES,
    normalize,
    rlm,
    t,
)

# Reuse the handler-level fakes exactly as the other handler tests do.
import test_handlers as harness  # noqa: E402  (pytest puts tests/ on sys.path)

USER = harness.USER
FakeMessage = harness.FakeMessage
FakeCallback = harness.FakeCallback


@pytest.fixture
def services(database):
    """Same service bundle the handler tests use (see tests/test_handlers.py)."""
    from pathlib import Path

    from linktofile.application.active import ActiveJobs
    from linktofile.application.queue import DownloadQueue
    from linktofile.application.ratelimit import RateLimiter
    from linktofile.application.settings import SettingsService
    from linktofile.domain.policies import FormatPolicy
    from linktofile.infrastructure.persistence.sqlite import (
        SqliteStats,
        SqliteTickets,
    )

    settings_repo = SqliteSettings(database)
    users = SqliteUsers(database)
    users.register(USER.id)
    workflow = harness.FakeWorkflow()
    workflow.temp_dir = Path("/tmp")
    return SimpleNamespace(
        bot=SimpleNamespace(),
        settings=SettingsService(settings_repo),
        access=harness.FakeAccess(),
        membership=harness.FakeMembership(),
        tickets=SqliteTickets(database),
        users=users,
        stats=SqliteStats(database),
        settings_repo=settings_repo,
        queue=DownloadQueue(concurrency=1, per_user_limit=5, max_size=10),
        workflow=workflow,
        policy=FormatPolicy(cap_bytes=1024 * 1024),
        limiter=RateLimiter(0),
        active=ActiveJobs(),
        prefs=UserPreferences(settings_repo),
        preview=harness.StubPreview(),
        cap_mb=50,
        admin_id=1,
        version="3.4.0",
        is_admin=lambda user_id: user_id == 1,
    )

FA_WELCOME = CATALOG["welcome"]["fa"]


class FakeState:
    """The slice of FSMContext the start / nav handlers touch."""

    def __init__(self) -> None:
        self.data: dict = {}
        self.cleared = 0

    async def clear(self) -> None:
        self.cleared += 1
        self.data.clear()

    async def set_state(self, state) -> None:
        self.data["state"] = state

    async def update_data(self, **kwargs) -> None:
        self.data.update(kwargs)

    async def get_data(self) -> dict:
        return dict(self.data)


# --- (a) catalog parity: every key speaks both languages, differently ---------
def test_every_catalog_key_has_distinct_en_and_fa():
    assert set(LANGUAGES) == {"en", "fa"}
    assert DEFAULT_LANGUAGE == "en"
    problems = []
    for key, entry in CATALOG.items():
        if set(entry) != set(LANGUAGES):
            problems.append(f"{key}: missing {sorted(set(LANGUAGES) - set(entry))}")
        elif entry["en"] == entry["fa"]:
            problems.append(f"{key}: identical in both languages")
    assert problems == []


# --- (b) placeholder parity: the same {placeholders} on both sides --------------
def test_placeholder_names_match_across_languages():
    pattern = re.compile(r"\{(\w+)\}")
    problems = []
    for key, entry in CATALOG.items():
        en, fa = pattern.findall(entry["en"]), pattern.findall(entry["fa"])
        if set(en) != set(fa):
            problems.append(f"{key}: {sorted(en)} != {sorted(fa)}")
    assert problems == []


def test_fa_copy_is_real_persian_not_transliteration():
    assert "سلام" in FA_WELCOME
    assert any("آ" <= ch <= "ی" or ch == "ی" for ch in FA_WELCOME)


# --- (c) t() fallback behavior ---------------------------------------------------
def test_t_renders_placeholders_and_falls_back_to_english():
    assert t("rate_limited", "en", seconds=7) == "⏳ Slow down a touch — try again in <b>7s</b>."
    assert "7 ثانیه" in t("rate_limited", "fa", seconds=7)
    assert t("rate_limited", "fa", seconds=7) != t("rate_limited", "en", seconds=7)


def test_t_falls_back_to_english_when_the_fa_value_is_missing(monkeypatch):
    entries = dict(CATALOG)
    entries["only_en"] = {"en": "English only {x}"}
    monkeypatch.setattr("linktofile.infrastructure.telegram.i18n.CATALOG", entries)
    assert t("only_en", "fa", x=1) == "English only 1"


def test_t_returns_the_key_itself_for_unknown_keys():
    assert t("totalmente_unknown_key") == "totalmente_unknown_key"
    assert t("totalmente_unknown_key", "fa") == "totalmente_unknown_key"


def test_t_never_raises_on_an_incomplete_format():
    template = CATALOG["welcome"]["en"]
    assert t("welcome", "en", name="Ada") == template


def test_t_returns_the_raw_template_when_the_format_code_is_broken(monkeypatch):
    entries = dict(CATALOG)
    entries["broken"] = {"en": "value: {x!q}", "fa": "مقدار: {x!q}"}
    monkeypatch.setattr("linktofile.infrastructure.telegram.i18n.CATALOG", entries)

    assert t("broken", "en", x=1) == "value: {x!q}"  # !q is not a format code
    assert t("broken", "fa", x=1) == "مقدار: {x!q}"


# --- (d) normalize() --------------------------------------------------------------
@pytest.mark.parametrize(
    "code,expected",
    [
        ("fa", "fa"),
        ("fa-IR", "fa"),
        ("fa_IR", "fa"),
        ("fa-ANY-cirilic", "fa"),
        ("FA-IR", "fa"),
        ("en", "en"),
        ("en-US", "en"),
        ("de", "en"),
        ("zh-Hans", "en"),
        ("", "en"),
        (None, "en"),
        ("  ", "en"),
    ],
)
def test_normalize_maps_everything_onto_the_catalog(code, expected):
    assert normalize(code) == expected


# --- (e) the RTL mark only travels with Persian ------------------------------------
@pytest.mark.parametrize(
    "lang,expected",
    [("fa", "\u200f"), ("fa-IR", "\u200f"), ("en", ""), ("de", "")],
)
def test_rlm_is_the_rtl_mark_only_for_persian(lang, expected):
    assert rlm(lang) == expected


def test_persian_messages_carry_the_rtl_mark_once_and_english_none():
    fa_welcome = texts.welcome(SimpleNamespace(first_name="Ada"), cap_mb=50, lang="fa")
    assert fa_welcome.startswith("\u200f") and fa_welcome.count("\u200f") == 1

    en_welcome = texts.welcome(SimpleNamespace(first_name="Ada"), cap_mb=50)
    assert "\u200f" not in en_welcome
    assert en_welcome == texts.welcome(SimpleNamespace(first_name="Ada"), cap_mb=50, lang="en")


def test_english_defaults_keep_the_existing_surface_wording():
    assert texts.cancel_text() == "🛑 <b>Cancelled.</b> Nothing is left running."
    assert t("btn_back", "en") == "⬅️ Back"
    assert t("btn_retry", "en") == "🔄 Try again"
    assert (
        [b.text for row in keyboards.main_menu().inline_keyboard for b in row][0]
        == "✨ What I can fetch"
    )


def test_english_and_persian_keyboards_follow_the_catalog():
    en_labels = {b.text for row in keyboards.main_menu().inline_keyboard for b in row}
    fa_labels = {b.text for row in keyboards.main_menu(lang="fa").inline_keyboard for b in row}
    assert t("btn_language", "en") in en_labels
    assert t("btn_language", "fa") in fa_labels
    assert en_labels != fa_labels
    assert t("btn_home", "fa") in {
        b.text for row in keyboards.language_menu(lang="fa").inline_keyboard for b in row
    }


class DownloadTicketStub(SimpleNamespace):
    """history_menu only reads ``id`` and calls texts.history_label(ticket)."""


def test_the_history_button_keeps_its_ticket_label_and_replay_mark():
    tickets = [
        DownloadTicketStub(id=3, filename="video.mp4"),
        DownloadTicketStub(id=4, filename="song.mp3"),
    ]
    rows = keyboards.history_menu(tickets, lang="fa").inline_keyboard
    assert rows[0][0].text == "🔁 video.mp4"
    assert rows[1][0].text == "🔁 song.mp3"
    assert Nav.unpack(rows[-1][0].callback_data).action == "home"


def test_the_language_picker_offers_both_names_and_a_way_home():
    markup = keyboards.language_menu(lang="en")
    top = [b.text for b in markup.inline_keyboard[0]]
    assert top == [LANGUAGE_NAMES["en"], LANGUAGE_NAMES["fa"]]
    data = [LangCB.unpack(b.callback_data).action for b in markup.inline_keyboard[0]]
    assert data == ["en", "fa"]
    # every screen keeps its escape hatches
    last = [Nav.unpack(b.callback_data).action for b in markup.inline_keyboard[-1]]
    assert last == ["home", "close"]


# --- (g) handler level: a stored fa user gets Persian everywhere -------------------
async def test_a_fa_user_gets_the_persian_welcome_and_menu(services):
    services.prefs.set_language(USER.id, "fa")
    message = FakeMessage()

    await handlers.cmd_start(message, services, FakeState())

    assert "سلام" in message.answers[0]
    assert message.answers[0].startswith("\u200f")
    labels = [b.text for row in message.markups[-1].inline_keyboard for b in row]
    assert t("btn_history", "fa") in labels
    assert t("btn_sites", "fa") in labels


@pytest.mark.parametrize("command", ["help", "stats", "history", "lang"])
async def test_every_screen_renders_in_persian_for_a_fa_user(services, command):
    services.prefs.set_language(USER.id, "fa")
    message = FakeMessage()

    if command == "help":
        await handlers.cmd_help(message, services)
    elif command == "stats":
        await handlers.cmd_stats(message, services)
    elif command == "history":
        await handlers.cmd_history(message, services)
    else:
        await handlers.cmd_lang(message, services)

    assert "\u200f" in message.answers[-1], command


async def test_a_fa_user_gets_the_persian_history_screen(services):
    services.prefs.set_language(USER.id, "fa")
    services.tickets.create(USER.id, "https://example.com/a.zip")
    message = FakeMessage()

    await handlers.cmd_history(message, services)

    assert CATALOG["history_title"]["fa"] in message.answers[-1]


async def test_a_direct_download_card_stays_persian_for_a_fa_user(services):
    services.queue.start()
    services.prefs.set_language(USER.id, "fa")
    message = FakeMessage("https://example.com/file.zip")

    await handlers.on_text(message, services)
    await services.queue.drain(timeout=5)
    await services.queue.stop()

    assert "\u200f" in message.answers[-1]
    assert "در صف" in message.answers[-1]


async def test_a_failed_delivery_shows_the_persian_error_card():
    class FakeBot:
        async def send_message(self, *args, **kwargs):
            return None

    message = FakeMessage()
    card = TelegramDelivery(
        FakeBot(), message, ticket_id=3, header="H", media=False, admin_id=None, lang="fa"
    )

    await card.failed(MediaUnavailable("boom"))

    fa_card = texts.failed_card_text("boom", lang="fa")
    assert fa_card.startswith("\u200f") and "نشد" in fa_card
    labels = [b.text for row in message.markups[-1].inline_keyboard for b in row]
    assert t("btn_retry", "fa") in labels
    assert t("btn_home", "fa") in labels


async def test_a_fa_preview_card_is_persian():
    info = MediaInfo(title="A talk", uploader="Ada", duration=95, site="youtube.com")
    fa_caption = texts.preview_caption(info, lang="fa")
    assert "\u200f" in fa_caption
    assert CATALOG["pick_quality"]["fa"] in fa_caption


# --- /lang: persis, re-render and speak back in the chosen language ---------------
async def test_choosing_a_language_persists_and_renders_in_it(services):
    message = FakeMessage()
    callback = FakeCallback(message)

    await handlers.on_lang(callback, LangCB(action="fa"), services)

    assert services.prefs.language(USER.id) == "fa"
    assert "\u200f" in message.edits[-1]
    assert CATALOG["language"]["fa"] in message.edits[-1]
    assert callback.alerts == [t("language_saved", "fa")]


async def test_choosing_english_restores_english(services):
    services.prefs.set_language(USER.id, "fa")
    message = FakeMessage()
    callback = FakeCallback(message)

    await handlers.on_lang(callback, LangCB(action="en"), services)

    assert services.prefs.language(USER.id) == "en"
    assert "Language" in message.edits[-1]
    assert callback.alerts == [t("language_saved", "en")]


async def test_the_lang_screen_is_also_reachable_from_the_menu(services):
    services.prefs.set_language(USER.id, "fa")
    message = FakeMessage()
    callback = FakeCallback(message)

    await handlers.on_nav(callback, Nav(action="lang"), services, FakeState())

    assert "\u200f" in message.edits[-1]
    # both buttons are on it, in their own languages
    top = [b.text for b in message.markups[-1].inline_keyboard[0]]
    assert top == [LANGUAGE_NAMES["en"], LANGUAGE_NAMES["fa"]]


async def test_the_main_menu_carries_the_language_button(services):
    message = FakeMessage()

    await handlers.cmd_start(message, services, FakeState())

    data = [b.callback_data for row in message.markups[-1].inline_keyboard for b in row]
    assert Nav(action="lang").pack() in data


# --- /start adopts Telegram's hint exactly once -------------------------------------
async def test_start_adopts_a_persian_telegram_language_hint(services):
    user = SimpleNamespace(id=USER.id, first_name="Ada", language_code="fa-IR")
    message = FakeMessage()
    message.from_user = user

    await handlers.cmd_start(message, services, FakeState())

    assert services.prefs.language(user.id) == "fa"
    assert "سلام" in message.answers[0]


async def test_start_never_overrides_a_language_already_stored(services):
    services.prefs.set_language(USER.id, "en")
    user = SimpleNamespace(id=USER.id, first_name="Ada", language_code="fa-IR")
    message = FakeMessage()
    message.from_user = user

    await handlers.cmd_start(message, services, FakeState())

    assert services.prefs.language(user.id) == "en"


async def test_start_ignores_an_english_hint_entirely(services):
    user = SimpleNamespace(id=USER.id, first_name="Ada", language_code="en-GB")
    message = FakeMessage()
    message.from_user = user

    await handlers.cmd_start(message, services, FakeState())

    assert not services.prefs.has_language(user.id)


async def test_language_is_per_user_not_per_chat(services):
    services.prefs.set_language(4242, "fa")
    message = FakeMessage()
    callback = FakeCallback(message)

    await handlers.on_lang(callback, LangCB(action="fa"), services)

    assert services.prefs.language(USER.id) == "fa"  # only the tapper moved
    assert services.prefs.language(4242) == "fa"


# --- (f) UserPreferences language persistence (real SQLite) -------------------------
async def test_language_persists_across_a_fresh_repository_instance(database):
    first = UserPreferences(SqliteSettings(database))
    first.set_language(11, "fa-IR")

    second = UserPreferences(SqliteSettings(database))  # as after a restart

    assert second.language(11) == "fa"
    assert second.has_language(11)
    assert not second.has_language(999)
    assert second.language(999) == DEFAULT_LANGUAGE


def test_set_language_normalizes_before_persisting(database):
    prefs = UserPreferences(SqliteSettings(database))
    assert prefs.set_language(12, "fa-IR") == "fa"
    assert UserPreferences(SqliteSettings(database)).language(12) == "fa"


def test_language_and_quality_keys_never_collide(database):
    repo = SqliteSettings(database)
    repo.set("user_lang.77", "fa")

    prefs = UserPreferences(repo)

    assert prefs.language(77) == "fa"
    assert prefs.get(77) is None


def test_quality_and_clear_do_not_touch_the_language(database):
    prefs = UserPreferences(SqliteSettings(database))
    prefs.set_language(8, "fa")
    prefs.clear(8)

    assert prefs.language(8) == "fa"


# --- quality prefs still work in both languages -------------------------------------
async def test_the_quality_picker_works_for_a_fa_user(services):
    services.prefs.set_language(USER.id, "fa")
    ticket = services.tickets.create(USER.id, "https://youtu.be/dQw4w9WgXcQ")
    callback = FakeCallback(FakeMessage())

    await handlers.on_quality(
        callback, QualityCB(kind="video", value="480", ticket=ticket.id), services
    )

    assert services.prefs.get(USER.id) == Quality.video(480)
    assert callback.alerts == [t("added_to_queue", "fa")]
