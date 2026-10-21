"""Per-user defaults, stored in the existing key-value settings table.

Only one preference today — the quality the user picked last — but the shape is
deliberately generic so a "send as document" or "default audio bitrate" flag can
land here later without a schema change.
"""

from __future__ import annotations

from typing import Optional

from linktofile.domain.models import Quality, QualityKind
from linktofile.domain.ports import SettingsRepository

KEY_PREFIX = "user_quality."


def _key(user_id: int) -> str:
    return f"{KEY_PREFIX}{int(user_id)}"


def encode(quality: Quality) -> str:
    if quality.kind is QualityKind.AUDIO:
        return f"audio:{quality.bitrate or 192}"
    return f"video:{quality.height}" if quality.height else "video:best"


def decode(raw: str) -> Optional[Quality]:
    kind, _, value = (raw or "").partition(":")
    try:
        if kind == "video":
            return Quality.video(None if value == "best" else int(value))
        if kind == "audio":
            return Quality.audio(int(value or 192))
    except (TypeError, ValueError):
        return None
    return None


class UserPreferences:
    def __init__(self, repo: SettingsRepository) -> None:
        self._repo = repo

    def get(self, user_id: int) -> Optional[Quality]:
        return decode(self._repo.get(_key(user_id), ""))

    def set(self, user_id: int, quality: Quality) -> None:
        self._repo.set(_key(user_id), encode(quality))

    def clear(self, user_id: int) -> None:
        self._repo.set(_key(user_id), "")
