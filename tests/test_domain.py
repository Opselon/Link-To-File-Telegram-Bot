"""Domain rules: no I/O, no Telegram, no database."""

import pytest

from linktofile.domain.errors import InvalidTransition, MediaTooLarge
from linktofile.domain.models import (
    DownloadStatus,
    DownloadTicket,
    ForceJoinPolicy,
    MediaAsset,
    Quality,
)
from linktofile.domain.policies import CLOUD_API_MAX_MB, FormatPolicy, delivery_cap_mb


def test_quality_labels():
    assert Quality.video(720).label == "720p"
    assert Quality.video(None).label == "best"
    assert Quality.audio(320).label == "320 kbps"


@pytest.mark.parametrize("height", [100, 5000, -1])
def test_quality_rejects_impossible_heights(height):
    with pytest.raises(ValueError):
        Quality.video(height)


def test_quality_rejects_impossible_bitrates():
    with pytest.raises(ValueError):
        Quality.audio(12)


def test_ticket_state_machine_only_moves_forward():
    ticket = DownloadTicket(id=1, user_id=2, url="https://example.com/x")
    ticket.start()
    ticket.uploading()
    ticket.complete()
    assert ticket.status is DownloadStatus.COMPLETED
    assert ticket.is_terminal
    with pytest.raises(InvalidTransition):
        ticket.start()


def test_terminal_states_are_terminal():
    assert DownloadStatus.COMPLETED.is_terminal
    assert DownloadStatus.FAILED.is_terminal
    assert DownloadStatus.CANCELLED.is_terminal
    assert not DownloadStatus.PENDING.is_terminal


def test_delivery_cap_respects_telegram_cloud_limit():
    assert delivery_cap_mb(2000, has_local_api=False) == CLOUD_API_MAX_MB
    assert delivery_cap_mb(200, has_local_api=False) == 50
    assert delivery_cap_mb(2000, has_local_api=True) == 2000
    assert delivery_cap_mb(0, has_local_api=False) == 1


def test_policy_prefers_progressive_single_file():
    policy = FormatPolicy(cap_bytes=50 * 1024 * 1024)
    selector = policy.selector(Quality.video(None))
    assert selector.startswith("b[ext=mp4][filesize<")
    assert "+ba" in selector  # DASH only as a fallback


def test_policy_audio_uses_extract_postprocessor():
    policy = FormatPolicy(cap_bytes=10 * 1024 * 1024)
    options = policy.options(Quality.audio(128))
    assert options["postprocessors"][0]["preferredquality"] == "128"
    assert options["merge_output_format"] == "mp3"


def test_policy_size_acceptance():
    policy = FormatPolicy(cap_bytes=1000)
    assert policy.accepts(999)
    assert not policy.accepts(1001)
    assert not policy.accepts(0)
    assert policy.cap_mb == 0


def test_force_join_needs_both_switch_and_channel():
    assert not ForceJoinPolicy(enabled=True).is_active
    assert not ForceJoinPolicy(channel="@x").is_active
    assert ForceJoinPolicy(enabled=True, channel="@x").is_active


def test_media_asset_suffix_is_lowercase():
    asset = MediaAsset(path=__import__("pathlib").Path("A.MP4"), size=1, title="t")
    assert asset.suffix == ".mp4"


def test_media_too_large_is_a_bot_error():
    from linktofile.domain.errors import BotError

    assert issubclass(MediaTooLarge, BotError)


def test_quality_round_trips_through_the_durable_queue():
    from linktofile.domain.models import Quality

    assert Quality.decode(Quality.video(720).encode()) == Quality.video(720)
    assert Quality.decode(Quality.video(None).encode()) == Quality.video(None)
    assert Quality.decode(Quality.audio(192).encode()) == Quality.audio(192)
    assert Quality.decode("garbage") is None


def test_queued_is_an_active_non_terminal_state():
    from linktofile.domain.models import DownloadStatus

    assert DownloadStatus.QUEUED.is_terminal is False
    assert DownloadStatus.QUEUED.is_active is True
    assert DownloadStatus.DOWNLOADING.is_active is True
    assert DownloadStatus.COMPLETED.is_active is False


def test_failure_kind_mapper_records_the_truth():
    from linktofile.domain.errors import (
        BotError,
        StalledDownload,
        TransientDownloadFailed,
    )
    from linktofile.domain.models import FailureKind, failure_kind_of

    assert failure_kind_of(StalledDownload("stalled")) == FailureKind.STALLED.value
    assert failure_kind_of(TransientDownloadFailed("reset")) == FailureKind.NETWORK.value
    assert failure_kind_of(BotError("plain")) == FailureKind.UNKNOWN.value
    assert failure_kind_of(Exception("not ours")) == FailureKind.UNKNOWN.value
