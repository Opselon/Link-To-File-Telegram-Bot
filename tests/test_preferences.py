"""Remembered per-user defaults, stored in the settings key-value table."""

from __future__ import annotations

from linktofile.application.preferences import UserPreferences, decode, encode
from linktofile.domain.models import Quality
from linktofile.infrastructure.persistence.sqlite import SqliteSettings


def test_encoding_round_trips_every_shape():
    for quality in (
        Quality.video(2160),
        Quality.video(None),
        Quality.audio(320),
        Quality.audio(64),
    ):
        assert decode(encode(quality)) == quality


def test_garbage_decodes_to_none():
    for raw in ("", "nonsense", "video:", "video:99999", "audio:zero", "podcast:1"):
        assert decode(raw) is None


def test_preferences_persist_per_user(database):
    prefs = UserPreferences(SqliteSettings(database))

    assert prefs.get(1) is None
    prefs.set(1, Quality.video(1080))
    prefs.set(2, Quality.audio(320))

    assert prefs.get(1) == Quality.video(1080)
    assert prefs.get(2) == Quality.audio(320)
    assert prefs.get(3) is None


def test_clear_forgets_the_preference(database):
    prefs = UserPreferences(SqliteSettings(database))
    prefs.set(7, Quality.video(720))

    prefs.clear(7)

    assert prefs.get(7) is None


def test_a_corrupt_row_is_ignored_not_raised(database):
    repo = SqliteSettings(database)
    repo.set("user_quality.5", "banana")

    assert UserPreferences(repo).get(5) is None
