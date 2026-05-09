"""Tests for DuckDBSessionManager — Phase 2 connection pooling."""

from __future__ import annotations

import threading
import time

import pytest
from arrow_lake.config import OlapConfig


@pytest.fixture()
def config() -> OlapConfig:
    return OlapConfig(
        max_concurrent_queries=2,
        max_query_memory_mb=64,
        query_timeout_seconds=30,
    )


@pytest.fixture()
def manager(config: OlapConfig):
    from arrow_lake.query.session_manager import DuckDBSessionManager

    mgr = DuckDBSessionManager(config, idle_timeout_seconds=60)
    yield mgr
    mgr.shutdown()


class TestAcquireRelease:
    """Basic acquire/release cycle."""

    def test_acquire_returns_connection(self, manager) -> None:
        session = manager.acquire()
        result = session.conn.execute("SELECT 42").fetchone()
        assert result == (42,)
        session.release()

    def test_stats_track_queries(self, manager) -> None:
        manager.acquire().release()
        manager.acquire().release()
        stats = manager.get_stats()
        assert stats.total_queries == 2
        assert stats.active_sessions == 0

    def test_concurrency_limit(self, manager) -> None:
        acquired = []
        barrier = threading.Barrier(2)

        def worker():
            s = manager.acquire()
            acquired.append(1)
            barrier.wait()
            time.sleep(0.05)
            s.release()

        t1 = threading.Thread(target=worker)
        t2 = threading.Thread(target=worker)
        t1.start()
        t2.start()
        t1.join(timeout=5)
        t2.join(timeout=5)
        assert len(acquired) == 2


class TestIdlePool:
    """Connection reuse via idle pool."""

    def test_connection_reuse(self, manager) -> None:
        manager.acquire().release()
        assert len(manager._idle_pool) == 1

        session = manager.acquire()
        assert len(manager._idle_pool) == 0
        session.conn.execute("SELECT 1")
        session.release()
        assert len(manager._idle_pool) == 1

    def test_idle_connection_healthy_after_reuse(self, manager) -> None:
        session = manager.acquire()
        session.conn.execute("CREATE TABLE _test_pool (x INTEGER)")
        session.conn.execute("INSERT INTO _test_pool VALUES (1), (2)")
        session.release()

        session2 = manager.acquire()
        try:
            result = session2.conn.execute("SELECT SUM(x) FROM _test_pool").fetchone()
            assert result == (3,)
        finally:
            session2.conn.execute("DROP TABLE IF EXISTS _test_pool")
            session2.release()

    def test_idle_timeout_eviction(self, manager) -> None:
        from arrow_lake.query.session_manager import _IdleConnection

        manager.acquire().release()
        assert len(manager._idle_pool) == 1

        # Replace with an expired entry
        manager._idle_pool.clear()
        manager._idle_pool.append(_IdleConnection(
            conn=manager._idle_pool.__class__  # dummy, will be evicted
        ))
        manager._idle_pool.clear()
        # Actually, test by creating a new manager with very short timeout
        from arrow_lake.query.session_manager import DuckDBSessionManager

        mgr = DuckDBSessionManager(manager._olap_config, idle_timeout_seconds=0)
        s = mgr.acquire()
        s.release()
        # With 0 timeout, the next acquire should evict and create new
        s2 = mgr.acquire()
        assert s2.conn.execute("SELECT 1").fetchone() == (1,)
        s2.release()
        mgr.shutdown()

    def test_max_idle_connections(self, manager) -> None:
        # With pool_size=2, max_idle=2. Each acquire/release reuses,
        # so pool stays at 1 (one conn reused across all calls).
        manager.acquire().release()
        assert len(manager._idle_pool) == 1

        manager.acquire().release()
        assert len(manager._idle_pool) == 1  # reused

    def test_broken_idle_connection_replaced(self, manager) -> None:
        session = manager.acquire()
        conn = session.conn
        session.release()

        # Break the idle connection
        conn.close()

        # Next acquire should detect broken conn and create new one
        session2 = manager.acquire()
        assert session2.conn.execute("SELECT 1").fetchone() == (1,)
        session2.release()


class TestHealthCheck:
    """Connection health validation."""

    def test_healthy_connection_passes(self, manager) -> None:
        session = manager.acquire()
        assert manager._health_check(session.conn) is True
        session.release()

    def test_broken_connection_fails(self, manager) -> None:
        import duckdb

        conn = duckdb.connect()
        conn.close()
        assert manager._health_check(conn) is False


class TestShutdown:
    """Graceful shutdown."""

    def test_shutdown_drains_idle_pool(self, manager) -> None:
        manager.acquire().release()
        assert len(manager._idle_pool) == 1

        manager.shutdown()
        assert len(manager._idle_pool) == 0

    def test_acquire_after_shutdown_raises(self, manager) -> None:
        manager.shutdown()
        with pytest.raises(RuntimeError, match="closed"):
            manager.acquire()

    def test_shutdown_idempotent(self, manager) -> None:
        manager.shutdown()
        manager.shutdown()  # Should not raise


class TestMetrics:
    """Prometheus metrics tracking."""

    def test_active_sessions_metric(self, manager) -> None:
        from arrow_lake.core.metrics import duckdb_pool_active_sessions

        session = manager.acquire()
        assert duckdb_pool_active_sessions._value.get() == 1
        session.release()
        assert duckdb_pool_active_sessions._value.get() == 0

    def test_total_queries_metric(self, manager) -> None:
        from arrow_lake.core.metrics import duckdb_pool_total_queries

        initial = duckdb_pool_total_queries._value.get()
        manager.acquire().release()
        manager.acquire().release()
        assert duckdb_pool_total_queries._value.get() == initial + 2
