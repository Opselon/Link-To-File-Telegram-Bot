"""Domain model: pure data and rules, no Telegram, no SQLite, no I/O."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Optional


class DownloadStatus(str, Enum):
    PENDING = "pending"
    DOWNLOADING = "downloading"
    UPLOADING = "uploading"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"

    @property
    def is_terminal(self) -> bool:
        return self in {self.COMPLETED, self.FAILED, self.CANCELLED}


class QualityKind(str, Enum):
    VIDEO = "video"
    AUDIO = "audio"


@dataclass(frozen=True, slots=True)
class Quality:
    """A user's requested output; ``height``/``bitrate`` may be empty."""

    kind: QualityKind
    height: Optional[int] = None
    bitrate: Optional[int] = None

    def __post_init__(self) -> None:
        if self.kind is QualityKind.VIDEO and self.height is not None:
            if not 144 <= self.height <= 4320:
                raise ValueError(f"unsupported height: {self.height}")
        if self.kind is QualityKind.AUDIO and self.bitrate is not None:
            if not 32 <= self.bitrate <= 320:
                raise ValueError(f"unsupported bitrate: {self.bitrate}")

    @classmethod
    def video(cls, height: Optional[int]) -> "Quality":
        return cls(kind=QualityKind.VIDEO, height=height)

    @classmethod
    def audio(cls, bitrate: Optional[int] = 192) -> "Quality":
        return cls(kind=QualityKind.AUDIO, bitrate=bitrate)

    @property
    def label(self) -> str:
        if self.kind is QualityKind.AUDIO:
            return f"{self.bitrate} kbps" if self.bitrate else "audio"
        return f"{self.height}p" if self.height else "best"


@dataclass(slots=True)
class MediaAsset:
    """The file produced by the download engine."""

    path: Path
    size: int
    title: str
    duration: Optional[int] = None
    width: Optional[int] = None
    height: Optional[int] = None
    artist: Optional[str] = None
    source: str = "direct"

    @property
    def suffix(self) -> str:
        return self.path.suffix.lower()


@dataclass(slots=True)
class DownloadTicket:
    """Aggregate root for one user request."""

    id: int
    user_id: int
    url: str
    status: DownloadStatus = DownloadStatus.PENDING
    filename: Optional[str] = None
    size: Optional[int] = None
    quality: Optional[Quality] = None

    def start(self) -> None:
        self._move_to(DownloadStatus.DOWNLOADING)

    def uploading(self) -> None:
        self._move_to(DownloadStatus.UPLOADING)

    def complete(self) -> None:
        self._move_to(DownloadStatus.COMPLETED)

    def fail(self) -> None:
        self._move_to(DownloadStatus.FAILED)

    def cancel(self) -> None:
        self._move_to(DownloadStatus.CANCELLED)

    def _move_to(self, target: DownloadStatus) -> None:
        from linktofile.domain.errors import InvalidTransition

        allowed = _TRANSITIONS.get(self.status, set())
        if target not in allowed:
            raise InvalidTransition(f"{self.status.value} -> {target.value}")
        self.status = target

    @property
    def is_terminal(self) -> bool:
        return self.status.is_terminal


_TRANSITIONS: dict[DownloadStatus, set[DownloadStatus]] = {
    DownloadStatus.PENDING: {
        DownloadStatus.DOWNLOADING,
        DownloadStatus.CANCELLED,
        DownloadStatus.FAILED,
    },
    DownloadStatus.DOWNLOADING: {
        DownloadStatus.UPLOADING,
        DownloadStatus.CANCELLED,
        DownloadStatus.FAILED,
    },
    DownloadStatus.UPLOADING: {
        DownloadStatus.COMPLETED,
        DownloadStatus.CANCELLED,
        DownloadStatus.FAILED,
    },
}


@dataclass(frozen=True, slots=True)
class MediaInfo:
    """Metadata shown on the preview card, before a single byte is downloaded.

    Pure data: the probe fills it in, the Telegram surface renders it, and the
    quality picker is attached underneath. A failed probe simply yields ``None``.
    """

    title: str
    uploader: Optional[str] = None
    duration: Optional[int] = None
    thumbnail: Optional[str] = None
    site: str = ""
    url: str = ""
    view_count: Optional[int] = None

    @property
    def has_thumbnail(self) -> bool:
        """Telegram only accepts http(s) URLs as a photo source."""
        return bool(self.thumbnail and self.thumbnail.startswith(("http://", "https://")))


@dataclass(frozen=True, slots=True)
class ForceJoinPolicy:
    """Global join gate, editable from the admin dashboard."""

    enabled: bool = False
    channel: str = ""
    invite_url: str = ""

    @property
    def is_active(self) -> bool:
        return bool(self.enabled and self.channel)


@dataclass(frozen=True, slots=True)
class QueueSnapshot:
    active: int
    pending: int
