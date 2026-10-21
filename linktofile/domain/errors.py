"""Domain and application errors. Messages are safe to show to a user."""

from __future__ import annotations


class BotError(Exception):
    """Base class for everything the UI is allowed to render verbatim."""


class InvalidUrl(BotError):
    pass


class UnsafeUrl(BotError):
    pass


class InvalidTransition(BotError):
    pass


class MediaTooLarge(BotError):
    pass


class DownloadFailed(BotError):
    pass


class DownloadCancelled(BotError):
    def __init__(self, message: str = "Download cancelled.") -> None:
        super().__init__(message)


class QueueSaturated(BotError):
    pass


class NotEnoughDisk(BotError):
    pass


class ChannelNotConfigured(BotError):
    pass
