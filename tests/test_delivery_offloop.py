"""Progress hooks must never kill a ticket.

yt-dlp runs in a worker thread (``run_in_executor``) and its progress hook is
called on that thread. ``TelegramDelivery.progress`` used to call
``asyncio.create_task`` there, which raises ``RuntimeError: no running event
loop`` inside the executor — the exception surfaced from the download itself
and the ticket failed with "Could not process this link. no running event loop".
"""

import asyncio

from linktofile.infrastructure.telegram.delivery import Card, TelegramDelivery


class _Message:
    def __init__(self) -> None:
        self.edits: list[str] = []

    async def edit_text(self, text: str, **kwargs) -> None:
        self.edits.append(text)


def _make_delivery(message: _Message) -> TelegramDelivery:
    card = Card(message)  # type: ignore[arg-type]
    return TelegramDelivery(
        bot=None,  # type: ignore[arg-type]
        message=message,  # type: ignore[arg-type]
        ticket_id=7,
        header="🎬 clip",
        media=True,
        card=card,
    )


async def test_progress_hook_from_a_worker_thread_updates_the_card():
    message = _Message()
    delivery = _make_delivery(message)

    def hook() -> None:
        # Exactly what yt-dlp's progress hook does: call back on its thread.
        delivery.progress(50, 100, 10.0, 5.0)

    await asyncio.get_running_loop().run_in_executor(None, hook)
    for _ in range(100):
        if message.edits:
            break
        await asyncio.sleep(0.02)

    assert message.edits, "the card was never refreshed from the worker thread"
    assert "Downloading" in message.edits[-1]


async def test_progress_hook_on_the_loop_also_works():
    message = _Message()
    delivery = _make_delivery(message)
    delivery.progress(1, 100, 1.0, 99.0)
    for _ in range(100):
        if message.edits:
            break
        await asyncio.sleep(0.02)
    assert message.edits


async def test_upload_progress_from_a_worker_thread_updates_the_card():
    message = _Message()
    delivery = _make_delivery(message)

    def hook() -> None:
        delivery._upload_progress(50, 100, 10.0, 5.0)

    await asyncio.get_running_loop().run_in_executor(None, hook)
    for _ in range(100):
        if message.edits:
            break
        await asyncio.sleep(0.02)

    assert message.edits, "the upload card was never refreshed from the worker thread"
    assert "Uploading" in message.edits[-1]
