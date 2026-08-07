"""Tests for _convert_with_timeout — the Docling convert() hard-timeout watchdog.

Covers the #1 ingest hang risk (2026-08 incident: worker stuck 2.5h on a
pathological PDF / GPU stall). Docling inference has no built-in timeout, so
convert() is run in a daemon worker bounded by a per-document ceiling; on
timeout the caller evicts the poisoned converter.
"""

from __future__ import annotations

import threading

import pytest

from arrow_lake.ingest.document import _DoclingConvertTimeout, _convert_with_timeout


def test_success_returns_result():
    # Arrange — a converter that finishes well within the budget.
    class _Conv:
        def convert(self, path):
            return f"md:{path}"

    # Act / Assert
    assert _convert_with_timeout(_Conv(), "/x.pdf", threading.RLock(), 2.0) == "md:/x.pdf"


def test_timeout_raises_when_convert_hangs():
    # Arrange — convert() hangs forever (waits on an Event we never set).
    done = threading.Event()

    class _Conv:
        def convert(self, path):
            done.wait(30)
            return "md"

    # Act / Assert
    with pytest.raises(_DoclingConvertTimeout):
        _convert_with_timeout(_Conv(), "/x.pdf", threading.RLock(), 0.1)
    done.set()  # release the leaked daemon worker


def test_exception_from_convert_propagates():
    # Arrange
    class _Conv:
        def convert(self, path):
            raise ValueError("bad pdf")

    # Act / Assert
    with pytest.raises(ValueError, match="bad pdf"):
        _convert_with_timeout(_Conv(), "/x.pdf", threading.RLock(), 2.0)


def test_lock_is_released_on_success():
    # Arrange — the lock must be acquireable again after a successful convert,
    # proving the worker released it (not left held by a leaked thread).
    lock = threading.RLock()

    class _Conv:
        def convert(self, path):
            return "md"

    _convert_with_timeout(_Conv(), "/x.pdf", lock, 2.0)
    # Act / Assert — re-acquire would block forever if the worker held it.
    acquired = lock.acquire(timeout=1.0)
    try:
        assert acquired
    finally:
        if acquired:
            lock.release()
