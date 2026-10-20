"""Shared test setup: isolate the data directory before config is imported."""

from __future__ import annotations

import os
import tempfile

import pytest

_TEST_DATA_DIR = tempfile.mkdtemp(prefix="link-to-file-tests-")
os.environ["DATA_DIR"] = _TEST_DATA_DIR
os.environ.setdefault("BOT_TOKEN", "123456:TEST-TOKEN-FOR-UNIT-TESTS-ONLY")
os.environ.setdefault("ADMIN_ID", "1")
os.environ.setdefault("RATE_LIMIT_SECONDS", "0")
os.environ.setdefault("QUEUE_CONCURRENCY", "2")


@pytest.fixture(autouse=True)
async def _close_http_session():
    """The pooled download session is loop-bound; close it per test."""
    yield
    import downloader

    await downloader.close_session()
