"""v3.2 regressions: the instant lane, the error taxonomy and pipeline logs.

Three defects are pinned here:

1. Instagram (and other short-video) links used to stop at a quality picker
   after a preview probe — pure latency for a site that serves one file per post.
2. ``engine.DownloadError`` was a plain ``Exception``, so every real cause fell
   through to "Something broke on my side" and the user never saw the reason.
3. Nothing in the log tied a stage to a ticket, so a failure had no paper trail.
"""

from __future__ import annotations

import logging
from types import SimpleNamespace

import pytest

from linktofile.application.active import ActiveJobs
from linktofile.application.preferences import UserPreferences
from linktofile.application.queue import DownloadQueue
from linktofile.application.ratelimit import RateLimiter
from linktofile.application.settings import SettingsService
from linktofile.domain.errors import BotError, LoginRequired, MediaUnavailable
from linktofile.domain.errors import DownloadCancelled as DomainCancelled
from linktofile.domain.models import Quality
from linktofile.domain.policies import FormatPolicy
import linktofile.infrastructure.download.engine as engine_module
from linktofile.infrastructure.download.engine import (
    DownloadCancelled,
    DownloadError,
    classify_ytdlp_failure,
    is_instant_url,
    proxy_for,
)
from linktofile.infrastructure.persistence.sqlite import (
    SqliteSettings,
    SqliteStats,
    SqliteTickets,
    SqliteUsers,
)
from linktofile.infrastructure.telegram import handlers, keyboards, texts
from linktofile.infrastructure.telegram.callbacks import TicketCB
from linktofile.infrastructure.telegram.delivery import TelegramDelivery
from linktofile.shared.telemetry import pipeline

USER = SimpleNamespace(id=7, first_name="Ada", is_bot=False)
IG_REEL = "https://www.instagram.com/reel/DewCYaBp1IR/"
IG_EMPTY = (
    "ERROR: [Instagram] DewCYaBp1IR: Instagram sent an empty media response. "
    "Check if this post is accessible in your browser without being logged-in. "
    "If it is not, then use --cookies-from-browser or --cookies for the authentication."
)


# --- fakes -------------------------------------------------------------------
class FakeMessage:
    def __init__(self, text: str = "") -> None:
        self.text = text
        self.chat = SimpleNamespace(id=7)
        self.from_user = USER
        self.answers: list[str] = []
        self.edits: list[str] = []
        self.captions: list[str] = []
        self.photos: list[str] = []
        self.photo = None
        self.markups: list = []
        self.deleted = False

    async def answer(self, text, **kwargs):
        self.answers.append(text)
        self.markups.append(kwargs.get("reply_markup"))
        return self

    async def answer_photo(self, photo, **kwargs):
        self.photos.append(str(photo))
        self.answers.append(kwargs.get("caption") or "")
        self.markups.append(kwargs.get("reply_markup"))
        return self

    async def edit_text(self, text, **kwargs):
        self.edits.append(text)
        self.markups.append(kwargs.get("reply_markup"))
        return self

    async def edit_caption(self, caption=None, **kwargs):
        self.captions.append(caption or "")
        self.markups.append(kwargs.get("reply_markup"))
        return self

    async def delete(self):
        self.deleted = True


class FakeCallback:
    def __init__(self, message: FakeMessage) -> None:
        self.message = message
        self.from_user = USER
        self.alerts: list[str] = []

    async def answer(self, text: str = "", show_alert: bool = False):
        if text:
            self.alerts.append(text)


class FakeBot:
    def __init__(self) -> None:
        self.messages: list[tuple] = []

    async def send_message(self, chat_id, text, **kwargs):
        self.messages.append((chat_id, text))


class FakeWorkflow:
    def __init__(self) -> None:
        self.jobs: list = []

    async def run(self, job, delivery, *, on_progress=None, cancel_event=None):
        self.jobs.append(job)

    async def close(self) -> None:
        return None


class FakeAccess:
    async def allows(self, user_id: int) -> bool:
        return True

    def forget_membership_cache(self) -> None:
        return None


class FakeMembership:
    async def resolve_channel(self, channel):
        return None


class StubPreview:
    def __init__(self) -> None:
        self.calls: list[str] = []

    async def describe(self, url: str):
        self.calls.append(url)
        return None


@pytest.fixture
def services(database, tmp_path):
    settings_repo = SqliteSettings(database)
    users = SqliteUsers(database)
    users.register(USER.id)
    return SimpleNamespace(
        bot=FakeBot(),
        settings=SettingsService(settings_repo),
        access=FakeAccess(),
        membership=FakeMembership(),
        tickets=SqliteTickets(database),
        users=users,
        stats=SqliteStats(database),
        settings_repo=settings_repo,
        queue=DownloadQueue(concurrency=1, per_user_limit=5, max_size=10),
        workflow=FakeWorkflow(),
        policy=FormatPolicy(cap_bytes=1024 * 1024),
        limiter=RateLimiter(0),
        active=ActiveJobs(),
        prefs=UserPreferences(settings_repo),
        preview=StubPreview(),
        cap_mb=50,
        admin_id=1,
        version="3.2.0",
        is_admin=lambda user_id: user_id == 1,
    )


# --- 1. the error taxonomy ----------------------------------------------------
def test_engine_errors_are_renderable_by_the_ui():
    """The transport error must be a BotError, or the reason never reaches the user."""
    assert issubclass(DownloadError, BotError)
    assert isinstance(DownloadError("nope"), BotError)
    # ... while a cancel still counts as a cancel, not as a failure.
    assert issubclass(DownloadCancelled, DomainCancelled)


def test_instagram_empty_response_is_media_unavailable():
    error = classify_ytdlp_failure(IG_EMPTY, host="www.instagram.com")
    assert isinstance(error, MediaUnavailable)
    assert "public" in str(error).lower()
    assert "cookies" not in str(error).lower()


def test_login_walls_on_other_sites_are_login_required():
    error = classify_ytdlp_failure(
        "ERROR: [youtube] Sign in to confirm you're not a bot", host="youtube.com"
    )
    assert isinstance(error, LoginRequired)


def test_size_limit_keeps_its_own_wording():
    error = classify_ytdlp_failure(
        "ERROR: File is larger than max-filesize (70.00MiB > 50.00MiB)", host="vimeo.com"
    )
    assert isinstance(error, DownloadError)
    assert "delivery limit" in str(error)


# --- 2. routing ---------------------------------------------------------------
@pytest.mark.parametrize(
    "url",
    [
        "https://www.instagram.com/reel/DewCYaBp1IR/",
        "https://instagram.com/reel/DewCYaBp1IR/?igsh=MTNkdjdpbDlnejU4cw==",
        "https://www.instagram.com/p/AbC123/",
        "https://ig.me/x1",
        "https://www.tiktok.com/@ada/video/123",
        "https://x.com/ada/status/123",
    ],
)
def test_short_video_links_are_instant(url):
    assert is_instant_url(url)


@pytest.mark.parametrize(
    "url",
    [
        "https://youtu.be/dQw4w9WgXcQ",
        "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
        "https://example.com/file.zip",
        "https://www.notinstagram.com/reel/x/",
    ],
)
def test_long_form_links_keep_the_picker(url):
    assert not is_instant_url(url)


def test_instant_hosts_bypass_the_shared_proxy(monkeypatch):
    """The shared proxy exists for YouTube's bot check; Instagram is faster direct."""
    monkeypatch.setattr(engine_module, "YTDLP_PROXY", "socks5://127.0.0.1:9050")
    monkeypatch.setattr(engine_module, "INSTANT_PROXY", None)

    assert proxy_for(IG_REEL) is None
    assert proxy_for("https://youtu.be/dQw4w9WgXcQ") == "socks5://127.0.0.1:9050"


def test_instant_options_carry_no_proxy(monkeypatch):
    monkeypatch.setattr(engine_module, "YTDLP_PROXY", "socks5://127.0.0.1:9050")
    monkeypatch.setattr(engine_module, "INSTANT_PROXY", None)

    assert "proxy" not in engine_module._build_ytdlp_options(1, url=IG_REEL)
    assert (
        engine_module._build_ytdlp_options(1, url="https://youtu.be/x")["proxy"]
        == "socks5://127.0.0.1:9050"
    )


def test_an_explicit_instant_proxy_is_honoured(monkeypatch):
    monkeypatch.setattr(engine_module, "YTDLP_PROXY", "socks5://127.0.0.1:9050")
    monkeypatch.setattr(engine_module, "INSTANT_PROXY", "http://127.0.0.1:3128")

    assert proxy_for(IG_REEL) == "http://127.0.0.1:3128"


async def test_an_instagram_link_is_queued_without_a_picker(services):
    services.queue.start()
    message = FakeMessage(IG_REEL)

    await handlers.on_text(message, services)
    await services.queue.drain(timeout=5)

    assert services.preview.calls == []  # no probe, no preview wait
    assert "Pick a quality" not in message.answers[0]
    assert "auto quality" in message.answers[0]
    assert "Instagram" in message.answers[0]
    assert len(services.workflow.jobs) == 1
    assert services.workflow.jobs[0].quality == Quality.video(None)
    await services.queue.stop()


async def test_youtube_still_asks_for_a_quality(services):
    message = FakeMessage("https://youtu.be/dQw4w9WgXcQ")

    await handlers.on_text(message, services)

    assert "Pick a quality" in message.answers[0]
    assert services.workflow.jobs == []


async def test_retrying_an_instagram_ticket_skips_the_picker(services):
    services.queue.start()
    ticket = services.tickets.create(USER.id, IG_REEL)
    message = FakeMessage()
    callback = FakeCallback(message)

    await handlers.on_ticket(callback, TicketCB(action="again", ticket=ticket.id), services)
    await services.queue.drain(timeout=5)

    assert "Pick a quality" not in message.edits
    assert len(services.workflow.jobs) == 1
    await services.queue.stop()


async def test_an_instagram_failure_hides_the_quality_button(services):
    message = FakeMessage()
    bot = FakeBot()
    delivery = TelegramDelivery(
        bot, message, ticket_id=9, header="⚡ Instagram", media=False, admin_id=1
    )

    await delivery.failed(MediaUnavailable("Instagram didn't hand back this media."))

    labels = [button.text for row in message.markups[-1].inline_keyboard for button in row]
    assert any("Try again" in label for label in labels)
    assert not any("Quality" in label for label in labels)
    assert bot.messages and "ticket 9" in bot.messages[0][1]


async def test_long_form_error_cards_keep_the_quality_shortcut():
    """Only instant-lane tickets lose the quality button on an error card."""
    labels = [
        button.text
        for row in keyboards.error_menu(4, media=True).inline_keyboard
        for button in row
    ]
    assert any("Quality" in label for label in labels)


# --- 3. pipeline logs ---------------------------------------------------------
def test_pipeline_records_are_greppable(caplog):
    with caplog.at_level(logging.INFO, logger="linktofile.pipeline"):
        pipeline("job.start", ticket=7, host="instagram.com", quality="best")

    line = caplog.records[-1].getMessage()
    assert line.startswith("[PIPELINE:job.start] ticket=7 ")
    assert "host=instagram.com" in line
    assert 'quality="best"' not in line  # plain values stay unquoted


def test_pipeline_quotes_free_text(caplog):
    with caplog.at_level(logging.WARNING, logger="linktofile.pipeline"):
        pipeline(
            "job.failed",
            ticket=3,
            reason="Instagram didn't hand back this media.",
            level=logging.WARNING,
        )

    line = caplog.records[-1].getMessage()
    assert 'reason="Instagram didn' in line
    assert caplog.records[-1].levelno == logging.WARNING


def test_the_instant_header_names_the_site():
    assert "Instagram" in texts.instant_header("www.instagram.com")
    assert "TikTok" in texts.instant_header("www.tiktok.com")
    assert "auto quality" in texts.instant_header("instagram.com")


async def test_a_failed_instagram_job_logs_its_category(services, tmp_path, caplog):
    """The workflow must log why, with the ticket, at warning level."""
    from linktofile.application.downloads import DownloadJob, DownloadWorkflow

    class FailingEngine:
        async def fetch(self, *args, **kwargs):
            raise MediaUnavailable("Instagram didn't hand back this media.")

        async def close(self):
            return None

    class Delivery:
        def __init__(self):
            self.errors = []

        async def delivering(self, asset):
            return None

        async def delivered(self, asset):
            return None

        async def failed(self, error):
            self.errors.append(error)

        async def cancelled(self):
            return None

    ticket = services.tickets.create(USER.id, IG_REEL)
    workflow = DownloadWorkflow(
        engine=FailingEngine(),
        tickets=services.tickets,
        policy=services.policy,
        temp_dir=tmp_path,
    )
    delivery = Delivery()

    with caplog.at_level(logging.INFO, logger="linktofile.pipeline"):
        await workflow.run(
            DownloadJob(
                ticket_id=ticket.id,
                user_id=USER.id,
                url=IG_REEL,
                quality=Quality.video(None),
            ),
            delivery,
        )

    lines = [record.getMessage() for record in caplog.records]
    assert any("[PIPELINE:job.start]" in line and "host=www.instagram.com" in line for line in lines)
    assert any("[PIPELINE:job.failed]" in line and "category=MediaUnavailable" in line for line in lines)
    assert isinstance(delivery.errors[0], MediaUnavailable)
