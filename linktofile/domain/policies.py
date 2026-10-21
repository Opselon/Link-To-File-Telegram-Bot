"""Pure policy objects: sizing rules and yt-dlp selectors."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from linktofile.domain.models import Quality, QualityKind

MB = 1024 * 1024
CLOUD_API_MAX_MB = 50


def delivery_cap_mb(configured_mb: int, has_local_api: bool) -> int:
    """What the configured Bot API can actually deliver.

    The cloud API tops out at 50 MB per file no matter what an operator
    configures, so downloading more is wasted bandwidth and disk.
    """
    if has_local_api:
        return max(1, min(configured_mb, 2000))
    return max(1, min(configured_mb, CLOUD_API_MAX_MB))


@dataclass(frozen=True, slots=True)
class FormatPolicy:
    """Builds the selectors handed to yt-dlp."""

    cap_bytes: int

    def selector(self, quality: Quality) -> str:
        if quality.kind is QualityKind.AUDIO:
            return "bestaudio/best"
        cap = self.cap_bytes
        if quality.height is None:
            # Progressive first: one file, no ffmpeg merge, half the disk.
            return (
                f"b[ext=mp4][filesize<{cap}]/"
                f"b[filesize<{cap}]/"
                "bv*[ext=mp4]+ba[ext=m4a]/bv*+ba/b"
            )
        height = quality.height
        return (
            f"b[height<={height}][ext=mp4][filesize<{cap}]/"
            f"b[height<={height}][filesize<{cap}]/"
            f"bv*[height<={height}][filesize<{cap}]+ba[filesize<{cap}]/"
            f"b[height<={height}][ext=mp4]/"
            f"b[height<={height}]/"
            f"b[filesize<{cap}]/b"
        )

    def options(self, quality: Quality) -> dict[str, Any]:
        options: dict[str, Any] = {
            "format": self.selector(quality),
            "merge_output_format": "mp4",
            "max_filesize": self.cap_bytes,
        }
        if quality.kind is QualityKind.AUDIO:
            options["merge_output_format"] = "mp3"
            options["postprocessors"] = [
                {
                    "key": "FFmpegExtractAudio",
                    "preferredcodec": "mp3",
                    "preferredquality": str(quality.bitrate or 192),
                }
            ]
        return options

    def accepts(self, size: int) -> bool:
        return 0 < size <= self.cap_bytes

    @property
    def cap_mb(self) -> int:
        return self.cap_bytes // MB
