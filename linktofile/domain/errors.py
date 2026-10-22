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


class TransientDownloadFailed(DownloadFailed):
    """A failure that is worth retrying (reset socket, 5xx, slow server).

    The retry policy lives in the workflow, not in the engine: one place owns
    the attempt count, the backoff and the pipeline log for it.
    """


class StalledDownload(TransientDownloadFailed):
    """No byte arrived for longer than the stall window."""


class MediaUnavailable(DownloadFailed):
    """The site answered, but handed back no media (private, gone, age-gated)."""


class LoginRequired(DownloadFailed):
    """The site wants a logged-in session before it releases the media."""


class DownloadCancelled(BotError):
    def __init__(self, message: str = "Download cancelled.") -> None:
        super().__init__(message)


class QueueSaturated(BotError):
    pass


class NotEnoughDisk(BotError):
    pass


class DuplicateJob(BotError):
    """An identical download is already queued or running for this user."""


class ChannelNotConfigured(BotError):
    pass
