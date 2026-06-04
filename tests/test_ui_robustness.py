import pytest
from utils import get_progress_bar

def test_get_progress_bar_with_nones():
    # This should not raise TypeError
    text = get_progress_bar(None, None, None, None)
    assert "0.0%" in text
    assert "Unknown" in text
    assert "0 B/s" in text
    assert "Calculating..." in text

def test_get_progress_bar_mixed():
    text = get_progress_bar(100, None, 50, None)
    assert "100.0 B / Unknown" in text
    assert "50.0 B/s" in text
