"""Tests for the generic ingest per-call timeout helpers.

_run_step_with_timeout (background embed+vector backfill) and _run_with_timeout
(kreuzberg parse) bound hung calls so the ingest task moves to FAILED instead
of idling in running forever.
"""

from __future__ import annotations

import threading
import time

import pytest

from arrow_lake._lake_ingest import _run_step_with_timeout
from arrow_lake.ingest.document import _run_with_timeout


def test_step_helper_returns_result():
    # Arrange
    def _quick():
        return 7
    # Act / Assert
    assert _run_step_with_timeout(_quick, timeout=2.0, label="t") == 7


def test_step_helper_raises_timeout_when_hung():
    # Arrange — hang forever
    done = threading.Event()

    def _hang():
        done.wait(30)

    # Act / Assert
    with pytest.raises(TimeoutError):
        _run_step_with_timeout(_hang, timeout=0.1, label="t")
    done.set()


def test_step_helper_propagates_exception():
    # Arrange / Act / Assert
    with pytest.raises(ValueError, match="boom"):
        _run_step_with_timeout(lambda: (_ for _ in ()).throw(ValueError("boom")), timeout=2.0, label="t")


def test_run_with_timeout_success():
    # Arrange
    def _work():
        time.sleep(0.02)
        return "parsed"
    # Act / Assert
    assert _run_with_timeout(_work, timeout=2.0, label="kreuzberg") == "parsed"


def test_run_with_timeout_raises_builtin_timeout():
    # Arrange — hang
    done = threading.Event()

    def _hang():
        done.wait(30)

    # Act / Assert — raises builtin TimeoutError (an OSError subclass)
    with pytest.raises(TimeoutError):
        _run_with_timeout(_hang, timeout=0.1, label="kreuzberg")
    done.set()
