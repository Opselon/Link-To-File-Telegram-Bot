import time

import pytest

from linktofile.shared import utils
from linktofile.shared.utils import (
    check_rate_limit,
    clean_filename,
    format_duration,
    format_eta,
    format_size,
    get_progress_bar,
    is_safe_url,
    is_valid_url,
    rate_limit_remaining,
)


@pytest.mark.parametrize(
    "url",
    [
        "https://example.com/file.zip",
        "http://example.com:8080/a/b?c=d",
        "https://youtu.be/dQw4w9WgXcQ",
    ],
)
def test_valid_urls(url):
    assert is_valid_url(url) is True


@pytest.mark.parametrize(
    "url",
    ["not a url", "", "ftp://example.com/x", "https://", "javascript:alert(1)", "https://exa mple.com"],
)
def test_invalid_urls(url):
    assert is_valid_url(url) is False


@pytest.mark.parametrize(
    "url",
    [
        "http://127.0.0.1/x",
        "http://localhost:9000/x",
        "http://10.0.0.5/x",
        "http://192.168.1.1/x",
        "http://169.254.169.254/latest/meta-data",
        "http://[::1]/x",
        "http://printer.local/x",
    ],
)
def test_private_targets_are_blocked(url):
    assert is_safe_url(url) is False


def test_public_targets_are_allowed():
    assert is_safe_url("https://example.com/file.zip") is True
    assert is_safe_url("http://93.184.216.34/x") is True


def test_rate_limit_window(monkeypatch):
    monkeypatch.setattr(utils, "RATE_LIMIT_SECONDS", 5)
    utils.user_last_request.clear()

    assert check_rate_limit(42) is True
    assert check_rate_limit(42) is False
    assert 0 < rate_limit_remaining(42) <= 5

    utils.user_last_request[42] = time.time() - 10
    assert check_rate_limit(42) is True


def test_format_size():
    assert format_size(0) == "0 B"
    assert format_size(999) == "999 B"
    assert format_size(1024) == "1.0 KB"
    assert format_size(1024 * 1024) == "1.0 MB"
    assert format_size(1024 * 1024 * 150) == "150 MB"


def test_format_time_helpers():
    assert format_eta(0) == "—"
    assert format_eta(45) == "45s"
    assert format_eta(125) == "2m 05s"
    assert format_eta(3700) == "1h 01m"
    assert format_duration(65) == "1:05"
    assert format_duration(3725) == "1:02:05"


def test_progress_bar_renders_percentage():
    text = get_progress_bar(50, 100, 1024, 10, stage="⬇️ Downloading")
    assert "50.0%" in text
    assert "⬇️ Downloading" in text
    assert "█" in text and "░" in text


def test_progress_bar_handles_unknown_total():
    text = get_progress_bar(10, 0, 0, 0)
    assert "?" in text


def test_clean_filename_strips_paths():
    assert clean_filename("../../etc/passwd") == "passwd"
    assert clean_filename("..") == "file"
    assert clean_filename("\x1b[31mevil\x1b[0m.zip") == "evil.zip"
    assert len(clean_filename("a" * 300 + ".zip")) <= 120
