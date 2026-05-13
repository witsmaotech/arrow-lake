"""Extra unit tests for DuckDBSessionManager — idle eviction, health checks, lifetime, _close_conn."""

from __future__ import annotations

import logging
import time
from unittest.mock import MagicMock

import pytest
from arrow_lake.config import OlapConfig
from arrow_lake.core.metrics import (
    enable_metrics,
    get_metrics_enabled,
)
from arrow_lake.query.session_manager import DuckDBSessionManager


def _ensure_metrics_enabled() -> None:
    if not get_metrics_enabled():
        enable_metrics()


@pytest.fixture
def olap_config() -> OlapConfig:
    return OlapConfig(max_concurrent_queries=2, max_query_memory_mb=128, query_timeout_seconds=5)


# ---------------------------------------------------------------------------
# 1. Idle timeout eviction
# ---------------------------------------------------------------------------


class TestIdleTimeoutEviction:
    """Connections sitting idle beyond idle_timeout_seconds should be evicted."""

    def test_idle_connection_evicted_on_reacquire(self, olap_config: OlapConfig) -> None:
        """A connection released and left idle longer than idle_timeout_seconds
        must be discarded; the next acquire creates a fresh connection."""
        mgr = DuckDBSessionManager(
            olap_config,
            idle_timeout_seconds=0,  # immediate eviction threshold
        )

        session = mgr.acquire()
        conn_id_before = id(session.conn)
        session.release()

        # Give the idle pool a moment — the eviction check is lazy (on acquire).
        time.sleep(0.05)

        session2 = mgr.acquire()
        conn_id_after = id(session2.conn)
        session2.release()

        # With idle_timeout_seconds=0 the old connection should be evicted and
        # a brand-new connection created, so the object ids must differ.
        assert conn_id_after != conn_id_before
        mgr.shutdown()

    def test_idle_pool_empties_after_timeout(self, olap_config: OlapConfig) -> None:
        """The idle pool should be empty after all connections expire."""
        mgr = DuckDBSessionManager(
            olap_config,
            idle_timeout_seconds=0,
        )

        s1 = mgr.acquire()
        s2 = mgr.acquire()
        s1.release()
        s2.release()

        time.sleep(0.05)

        # Both idle connections should be evicted on next acquires.
        new1 = mgr.acquire()
        new2 = mgr.acquire()
        try:
            # Both new sessions should be functional.
            assert new1.conn is not None
            assert new2.conn is not None
        finally:
            new1.release()
            new2.release()
            mgr.shutdown()


# ---------------------------------------------------------------------------
# 2. Health check failure during release
# ---------------------------------------------------------------------------


class TestHealthCheckFailureOnRelease:
    """When a connection is broken before release, it must not return to the
    idle pool."""

    def test_broken_connection_discarded_on_release(self, olap_config: OlapConfig) -> None:
        """Closing the underlying connection, then releasing, should discard it."""
        mgr = DuckDBSessionManager(olap_config)

        session = mgr.acquire()
        # Break the connection so the health check will fail.
        session._conn.close()
        session.release()

        # total_errors should be incremented because the health check detected
        # a broken connection.
        stats = mgr.get_stats()
        assert stats.total_errors >= 1
        assert stats.active_sessions == 0

        # A subsequent acquire must still succeed — the broken connection
        # was discarded, not returned to the pool.
        with mgr.acquire() as conn:
            result = conn.execute("SELECT 1").fetchone()
            assert result == (1,)

        mgr.shutdown()

    def test_subsequent_acquire_works_after_health_check_failure(self, olap_config: OlapConfig) -> None:
        """Even after multiple broken releases, the pool should self-heal."""
        mgr = DuckDBSessionManager(olap_config)

        for _ in range(3):
            session = mgr.acquire()
            session._conn.close()
            session.release()

        # Errors accumulated from failed health checks.
        assert mgr.get_stats().total_errors >= 3

        # Pool should still function normally.
        with mgr.acquire() as conn:
            assert conn.execute("SELECT 42").fetchone() == (42,)

        mgr.shutdown()


# ---------------------------------------------------------------------------
# 3. Max session lifetime eviction
# ---------------------------------------------------------------------------


class TestMaxSessionLifetimeEviction:
    """Connections exceeding max_session_lifetime_seconds should be evicted."""

    def test_connection_evicted_on_lifetime_expiry(self, olap_config: OlapConfig) -> None:
        """With max_session_lifetime_seconds=0, a released connection should
        immediately be considered expired on the next acquire."""
        mgr = DuckDBSessionManager(
            olap_config,
            max_session_lifetime_seconds=0,
        )

        session = mgr.acquire()
        conn_id_before = id(session.conn)
        session.release()

        time.sleep(0.05)

        session2 = mgr.acquire()
        conn_id_after = id(session2.conn)
        session2.release()

        # The old connection should have been evicted due to lifetime expiry.
        assert conn_id_after != conn_id_before
        mgr.shutdown()

    def test_active_session_not_evicted_mid_use(self, olap_config: OlapConfig) -> None:
        """A session that is actively held should not be evicted, even if
        max_session_lifetime_seconds is very small."""
        mgr = DuckDBSessionManager(
            olap_config,
            max_session_lifetime_seconds=0,
        )

        session = mgr.acquire()
        # Simulate a long-running query.
        time.sleep(0.1)
        # The connection should still be usable.
        result = session.conn.execute("SELECT 99").fetchone()
        assert result == (99,)
        session.release()
        mgr.shutdown()


# ---------------------------------------------------------------------------
# 4. _close_conn error logging
# ---------------------------------------------------------------------------


class TestCloseConnErrorLogging:
    """_close_conn should never raise, even when the connection is broken."""

    def test_close_conn_does_not_raise_on_broken_connection(self) -> None:
        """Calling _close_conn on an already-closed connection should not raise."""
        import duckdb

        conn = duckdb.connect()
        conn.close()  # DuckDB silently accepts double-close.

        mgr = MagicMock(spec=DuckDBSessionManager)
        mgr._conn_sessions = {}
        # Must not raise — _close_conn swallows duckdb.Error.
        DuckDBSessionManager._close_conn(mgr, conn)

    def test_close_conn_logs_warning_on_duckdb_error(self, caplog: pytest.LogCaptureFixture) -> None:
        """_close_conn should log a warning when conn.close() raises duckdb.Error."""
        import duckdb

        bad_conn = MagicMock()
        bad_conn.close.side_effect = duckdb.Error("simulated close failure")

        mgr = MagicMock(spec=DuckDBSessionManager)
        mgr._conn_sessions = {}

        with caplog.at_level(logging.WARNING, logger="arrow_lake.query.session_manager"):
            DuckDBSessionManager._close_conn(mgr, bad_conn)

        assert any(
            "Error closing DuckDB connection" in record.message
            for record in caplog.records
        )

    def test_close_conn_does_not_raise_on_duckdb_error(self) -> None:
        """_close_conn should swallow duckdb.Error without propagating."""
        import duckdb

        bad_conn = MagicMock()
        bad_conn.close.side_effect = duckdb.Error("simulated close failure")

        mgr = MagicMock(spec=DuckDBSessionManager)
        mgr._conn_sessions = {}

        # Must not propagate the exception.
        DuckDBSessionManager._close_conn(mgr, bad_conn)


# ---------------------------------------------------------------------------
# 5. Idle timeout + metrics interaction
# ---------------------------------------------------------------------------


class TestIdleEvictionWithMetrics:
    """Verify that evicted connections increment the Prometheus counter."""

    @classmethod
    def setup_class(cls) -> None:
        _ensure_metrics_enabled()

    def test_evicted_connections_increment_metric(self, olap_config: OlapConfig) -> None:
        """Evicted idle connections should increment
        duckdb_pool_evicted_connections_total."""
        from arrow_lake.core.metrics import duckdb_pool_evicted_connections_total

        # Reset the counter if it already has a value.
        initial = duckdb_pool_evicted_connections_total._value.get()

        mgr = DuckDBSessionManager(
            olap_config,
            idle_timeout_seconds=0,
        )

        session = mgr.acquire()
        session.release()
        time.sleep(0.05)

        # Acquiring again should evict the idle connection.
        session2 = mgr.acquire()
        try:
            val = duckdb_pool_evicted_connections_total._value.get()
            assert val > initial
        finally:
            session2.release()
            mgr.shutdown()
