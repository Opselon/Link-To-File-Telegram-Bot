"""Every keyboard must be complete and every button must carry a style."""

from __future__ import annotations

import pytest
from aiogram.types import InlineKeyboardButton

import ui

ALL_KEYBOARDS = {
    "main": ui.main_menu(),
    "main_admin": ui.main_menu(is_admin=True),
    "quality": ui.quality_menu(7),
    "progress": ui.progress_menu(7),
    "result": ui.result_menu(7),
    "result_media": ui.result_menu(7, media=True),
    "error": ui.error_menu(7),
    "cancel": ui.cancel_menu(7),
    "help": ui.help_menu(),
    "sites": ui.sites_menu(),
    "stats": ui.stats_menu(),
    "admin": ui.admin_menu(),
}


def _buttons(markup) -> list[InlineKeyboardButton]:
    return [button for row in markup.inline_keyboard for button in row]


@pytest.mark.parametrize("name", sorted(ALL_KEYBOARDS))
def test_every_button_is_styled(name):
    markup = ALL_KEYBOARDS[name]
    assert markup.inline_keyboard, f"{name} keyboard is empty"
    for button in _buttons(markup):
        assert button.style in {"primary", "success", "danger"}, (
            f"unstyled button in {name}: {button.text!r}"
        )


@pytest.mark.parametrize("name", sorted(ALL_KEYBOARDS))
def test_buttons_have_an_action(name):
    for button in _buttons(ALL_KEYBOARDS[name]):
        assert button.callback_data or button.url, f"dead button: {button.text!r}"


def test_style_support_is_detected():
    # aiogram >= 3.25 exposes `style`; the helper must agree with the model.
    assert ui._STYLE_SUPPORTED is ("style" in InlineKeyboardButton.model_fields)


def test_quality_keyboard_covers_video_and_audio():
    data = [button.callback_data for button in _buttons(ui.quality_menu(42))]
    for quality in ("2160", "1440", "1080", "720", "480", "360", "240", "144", "best"):
        assert f"q:mp4:{quality}:42" in data
    for bitrate in ("320", "192", "128"):
        assert f"q:mp3:{bitrate}:42" in data
    assert "job:cancel:42" in data


def test_main_menu_links_point_at_the_repo():
    urls = [button.url for button in _buttons(ui.main_menu()) if button.url]
    assert ui.REPO_URL in urls
    assert ui.ISSUES_URL in urls


def test_admin_button_only_for_admins():
    assert "menu:admin" not in [b.callback_data for b in _buttons(ui.main_menu())]
    assert "menu:admin" in [b.callback_data for b in _buttons(ui.main_menu(is_admin=True))]


def test_welcome_escapes_the_name():
    class User:
        first_name = "<script>"

    text = ui.welcome(User())
    assert "<script>" not in text
    assert "&lt;script&gt;" in text
    assert "Link → File" in text


def test_callback_data_fits_telegram_limit():
    for markup in ALL_KEYBOARDS.values():
        for button in _buttons(markup):
            if button.callback_data:
                assert len(button.callback_data.encode()) <= 64


def test_buttons_degrade_on_old_aiogram(monkeypatch):
    import ui

    monkeypatch.setattr(ui, "_STYLE_SUPPORTED", False)
    button = ui.btn("Plain", callback_data="menu:home", style="success")
    assert button.style is None
    assert button.callback_data == "menu:home"
