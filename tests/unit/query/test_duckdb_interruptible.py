"""Tests for run_duckdb_interruptible — the OLAP query timeout watchdog.

Covers the 2026-08-07 outage root cause: a stuck DuckDB/Lance scan must be
aborted via conn.interrupt() so the OLAP executor slot and DuckDB session are
released instead of wedging forever (which previously exhausted the pool and
froze all analytics until a process restart).
"""

from __future__ import annotations

import threading
import time
from unittest.mock import MagicMock

import duckdb
import pytest

from arrow_lake.config import OlapConfig
from arrow_lake.exceptions import ErrorCode, QueryError
from arrow_lake.query.session_manager import (
    DuckDBSessionManager,
    run_duckdb_interruptible,
)


def test_returns_result_when_func_completes_quickly():
    # Arrange
    conn = MagicMock()

    # Act
    result = run_duckdb_interruptible(conn, lambda: 42, timeout=5.0, label="t1")

    # Assert
    assert result == 42
    conn.interrupt.assert_not_called()


def test_timeout_raises_query_timeout_and_interrupts(monkeypatch):
    # Arrange — func blocks well beyond the timeout. Shrink the grace window
    # so the test is fast (the mock interrupt cannot unblock the Event.wait).
    monkeypatch.setattr(
        "arrow_lake.query.session_manager._INTERRUPT_GRACE_SECONDS", 0.1
    )
    done = threading.Event()
    conn = MagicMock()

    def slow() -> int:
        done.wait(30)
        return 1

    # Act / Assert
    start = time.monotonic()
    with pytest.raises(QueryError) as exc_info:
        run_duckdb_interruptible(conn, slow, timeout=0.2, label="t2")
    elapsed = time.monotonic() - start

    assert exc_info.value.error_code == ErrorCode.QUERY_TIMEOUT
    conn.interrupt.assert_called_once()
    # The caller thread is freed promptly (did not wait the full 30s).
    assert elapsed < 1.0
    done.set()  # release the orphaned daemon worker


def test_on_uninterruptible_invoked_when_query_survives_grace(monkeypatch):
    # Arrange — shrink the grace window so the test is fast. The slow func
    # never exits, so after grace the worker is still alive and the callback
    # must fire (this is the "stuck in native code" path).
    monkeypatch.setattr(
        "arrow_lake.query.session_manager._INTERRUPT_GRACE_SECONDS", 0.1
    )
    done = threading.Event()
    conn = MagicMock()
    called = threading.Event()

    def slow() -> int:
        done.wait(30)
        return 1

    def on_uninterruptible() -> None:
        called.set()

    # Act / Assert
    with pytest.raises(QueryError):
        run_duckdb_interruptible(
            conn,
            slow,
            timeout=0.1,
            label="t3",
            on_uninterruptible=on_uninterruptible,
        )
    assert called.is_set()
    done.set()


def test_func_exception_propagates_unchanged():
    # Arrange
    conn = MagicMock()

    def boom() -> None:
        raise ValueError("boom")

    # Act / Assert
    with pytest.raises(ValueError, match="boom"):
        run_duckdb_interruptible(conn, boom, timeout=5.0, label="t4")
    conn.interrupt.assert_not_called()


def test_returns_result_if_query_finishes_during_grace():
    """Boundary race: a query that outlives ``timeout`` but completes within the
    grace window must return its result, not a false timeout."""
    import time
    conn = MagicMock()

    def _slow_but_done():
        time.sleep(0.5)  # > timeout(0.2), well within timeout + grace(5.0)
        return "result"

    assert run_duckdb_interruptible(conn, _slow_but_done, timeout=0.2, label="boundary") == "result"


def test_real_duckdb_long_query_is_aborted_by_interrupt():
    """A genuinely long DuckDB scan must be interrupted within the grace window.

    Validates the core assumption: conn.interrupt() from the watchdog thread
    aborts an in-flight execute() on a real DuckDB connection.
    """
    # Arrange — count(*) over a 5B-row range scans for many seconds but stays
    # O(1) memory; DuckDB polls its interrupt flag between morsels.
    conn = duckdb.connect()
    long_sql = "SELECT count(*) FROM range(5000000000)"
    try:
        # Act / Assert
        start = time.monotonic()
        with pytest.raises(QueryError) as exc_info:
            run_duckdb_interruptible(
                conn,
                lambda: conn.execute(long_sql).arrow(),
                timeout=0.3,
                label="real",
            )
        elapsed = time.monotonic() - start

        assert exc_info.value.error_code == ErrorCode.QUERY_TIMEOUT
        # Interrupt worked: aborted well within timeout + grace + buffer,
        # not the full multi-second scan.
        assert elapsed < 0.3 + 6.0
    finally:
        conn.close()


def test_managed_session_mark_unhealthy_forces_close_on_release():
    """mark_unhealthy() must keep a suspect conn out of the idle pool.

    A connection whose query survived interrupt must be closed (not recycled),
    and the release path must skip its blocking SELECT 1 health check.
    """
    # Arrange
    mgr = DuckDBSessionManager(OlapConfig())
    try:
        # Act
        managed = mgr.acquire()
        conn = managed.conn
        managed.mark_unhealthy()
        managed.release()

        # Assert — conn was closed, idle pool stayed empty.
        assert len(mgr._idle_pool) == 0
        with pytest.raises(duckdb.Error):
            conn.execute("SELECT 1").fetchone()
    finally:
        mgr.shutdown()
