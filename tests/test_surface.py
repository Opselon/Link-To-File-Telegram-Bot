"""The Telegram surface: every screen is reachable, styled and escapable."""

import pytest

from linktofile.domain.models import ForceJoinPolicy, MediaAsset
from linktofile.infrastructure.telegram import keyboards, texts
from linktofile.infrastructure.telegram.callbacks import Nav, QualityCB, TicketCB
from linktofile.shared.utils import is_media_url, is_safe_url, is_valid_url

NAV_PREFIXES = ("nav:", "t:", "adm:", "join:")


def buttons(markup):
    return [button for row in markup.inline_keyboard for button in row]


def has_way_back(markup) -> bool:
    return any(
        (button.callback_data or "").startswith(NAV_PREFIXES) for button in buttons(markup)
    )


@pytest.mark.parametrize(
    "markup",
    [
        keyboards.main_menu(),
        keyboards.main_menu(is_admin=True),
        keyboards.quality_menu(1),
        keyboards.progress_menu(1),
        keyboards.result_menu(1),
        keyboards.result_menu(1, media=True),
        keyboards.error_menu(1),
        keyboards.cancel_menu(1),
        keyboards.help_menu(),
        keyboards.sites_menu(),
        keyboards.stats_menu(),
        keyboards.admin_menu(ForceJoinPolicy()),
        keyboards.join_menu(ForceJoinPolicy(channel="@x", invite_url="https://t.me/x")),
        keyboards.channel_set_menu(),
    ],
)
def test_every_screen_has_a_way_back(markup):
    assert markup.inline_keyboard
    assert has_way_back(markup)


def test_every_button_is_styled():
    for markup in (keyboards.main_menu(is_admin=True), keyboards.quality_menu(3)):
        for button in buttons(markup):
            assert getattr(button, "style", None) in {"primary", "success", "danger"}


def test_quality_menu_keeps_the_ticket_id():
    markup = keyboards.quality_menu(77)
    payloads = [
        QualityCB.unpack(b.callback_data)
        for b in buttons(markup)
        if (b.callback_data or "").startswith("q:")
    ]
    assert payloads and all(p.ticket == 77 for p in payloads)
    kinds = {p.kind for p in payloads}
    assert kinds == {"video", "audio"}


def test_progress_menu_offers_a_cancel():
    packed = [b.callback_data for b in buttons(keyboards.progress_menu(5))]
    assert any(data.startswith("t:cancel:5") or TicketCB.unpack(data).action == "cancel" for data in packed)


def test_home_navigation_is_packed():
    assert Nav.unpack(Nav(action="home").pack()).action == "home"


def test_welcome_escapes_user_names():
    class User:
        first_name = "<script>"

    rendered = texts.welcome(User())
    assert "<script>" not in rendered
    assert "&lt;script&gt;" in rendered


def test_admin_panel_reflects_the_policy():
    on = texts.admin_text(
        ForceJoinPolicy(enabled=True, channel="@chan"),
        {"users": 2, "total": 3, "today": 1, "completed": 2, "failed": 1, "bytes": 2048},
        active=1,
        pending=0,
        disk_free="9000 MB",
    )
    assert "🟢 on" in on and "@chan" in on
    off = texts.admin_text(
        ForceJoinPolicy(), {"users": 0, "total": 0, "today": 0, "completed": 0, "failed": 0, "bytes": 0},
        active=0,
        pending=0,
        disk_free="1 MB",
    )
    assert "⚪️ off" in off and "not set" in off


def test_done_caption_contains_the_useful_facts():
    asset = MediaAsset(path=__import__("pathlib").Path("a.mp4"), size=2 * 1024 * 1024,
                       title="Clip", duration=65, height=1080, source="youtube")
    caption = texts.done_caption(asset)
    assert "Clip" in caption
    assert "2.0 MB" in caption
    assert "1080p" in caption
    assert "1:05" in caption


@pytest.mark.parametrize(
    "url,expected",
    [
        ("https://youtu.be/abc", True),
        ("https://www.instagram.com/reel/x/", True),
        ("https://cdn.example.com/movie.mp4", True),
        ("https://example.com/file.zip", False),
        ("https://soundcloud.com/a/b", True),
        ("https://example.com/page.html", False),
    ],
)
def test_media_detection(url, expected):
    assert is_media_url(url) is expected


def test_ssrf_guard_blocks_local_targets():
    assert is_valid_url("https://example.com/a.zip")
    assert not is_safe_url("http://127.0.0.1:8000/a")
    assert not is_safe_url("http://169.254.169.254/latest/meta-data/")
    assert not is_safe_url("http://10.0.0.5/secret")


def test_join_menu_links_to_the_channel():
    markup = keyboards.join_menu(
        ForceJoinPolicy(enabled=True, channel="@c"), invite_url="https://t.me/c"
    )
    urls = [b.url for b in buttons(markup) if b.url]
    assert urls == ["https://t.me/c"]
