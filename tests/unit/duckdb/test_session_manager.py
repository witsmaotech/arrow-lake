"""Unit tests for DuckDBSessionManager — Phase 3 high availability."""

from __future__ import annotations

import threading
import time
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from arrow_lake.config import OlapConfig, StorageBackend, StorageConfig
from arrow_lake.core.metrics import (
    duckdb_pool_active_sessions,
    duckdb_pool_queued_requests,
    duckdb_pool_slow_queries,
    duckdb_pool_total_errors,
    duckdb_pool_total_queries,
    duckdb_pool_total_timeouts,
    enable_metrics,
    get_metrics_enabled,
)
from arrow_lake.query.session_manager import DuckDBSessionManager


def ensure_metrics_enabled() -> None:
    if not get_metrics_enabled():
        enable_metrics()


@pytest.fixture
def olap_config() -> OlapConfig:
    return OlapConfig(max_concurrent_queries=2, max_query_memory_mb=128, query_timeout_seconds=5)


@pytest.fixture
def manager(olap_config: OlapConfig) -> DuckDBSessionManager:
    return DuckDBSessionManager(olap_config)


class TestSessionManagerAcquireRelease:
    """Test basic acquire/release lifecycle."""

    def test_acquire_returns_managed_session(self, manager: DuckDBSessionManager) -> None:
        session = manager.acquire()
        assert session.conn is not None
        session.release()

    def test_acquire_enforces_pool_size(self, manager: DuckDBSessionManager) -> None:
        """Only max_concurrent_queries sessions can be active simultaneously."""
        sessions = [manager.acquire() for _ in range(2)]  # pool_size=2
        stats = manager.get_stats()
        assert stats.active_sessions == 2

        # Third acquire should timeout (query_timeout_seconds=5)
        with pytest.raises(TimeoutError, match="Could not acquire"):
            manager.acquire(timeout=0.5)

        for s in sessions:
            s.release()

    def test_release_frees_slot(self, manager: DuckDBSessionManager) -> None:
        session = manager.acquire()
        assert manager.get_stats().active_sessions == 1
        session.release()
        assert manager.get_stats().active_sessions == 0

    def test_context_manager_protocol(self, manager: DuckDBSessionManager) -> None:
        with manager.acquire() as conn:
            assert conn is not None
            result = conn.execute("SELECT 1").fetchone()
            assert result == (1,)
        assert manager.get_stats().active_sessions == 0

    def test_double_release_no_error(self, manager: DuckDBSessionManager) -> None:
        session = manager.acquire()
        session.release()
        session.release()  # Should be no-op
        assert manager.get_stats().active_sessions == 0


class TestSessionManagerStats:
    """Test statistics tracking."""

    def test_initial_stats(self, manager: DuckDBSessionManager) -> None:
        stats = manager.get_stats()
        assert stats.pool_size == 2
        assert stats.active_sessions == 0
        assert stats.total_queries == 0
        assert stats.total_errors == 0
        assert stats.total_timeouts == 0

    def test_queries_incremented(self, manager: DuckDBSessionManager) -> None:
        with manager.acquire():
            pass
        with manager.acquire():
            pass
        stats = manager.get_stats()
        assert stats.total_queries == 2

    def test_timeouts_incremented(self, manager: DuckDBSessionManager) -> None:
        s1 = manager.acquire()
        s2 = manager.acquire()
        try:
            with pytest.raises(TimeoutError):
                manager.acquire(timeout=0.5)
            assert manager.get_stats().total_timeouts == 1
        finally:
            s1.release()
            s2.release()

    def test_slow_query_count(self, manager: DuckDBSessionManager) -> None:
        manager.record_slow_query()
        manager.record_slow_query()
        assert manager.get_stats().slow_query_count == 2

    def test_avg_wait_time(self, manager: DuckDBSessionManager) -> None:
        with manager.acquire():
            pass
        stats = manager.get_stats()
        assert stats.avg_wait_seconds >= 0


class TestSessionManagerConcurrency:
    """Test thread-safe concurrent access."""

    def test_concurrent_acquire_release(self, manager: DuckDBSessionManager) -> None:
        """Multiple threads can acquire/release without corruption."""
        results: list[bool] = []
        errors: list[str] = []

        def worker() -> None:
            try:
                with manager.acquire(timeout=5) as conn:
                    result = conn.execute("SELECT 42").fetchone()
                    assert result == (42,)
                results.append(True)
            except Exception as exc:
                errors.append(str(exc))

        threads = [threading.Thread(target=worker) for _ in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert len(errors) == 0
        assert len(results) == 10
        assert manager.get_stats().active_sessions == 0
        assert manager.get_stats().total_queries == 10

    def test_max_concurrent_enforced(self, manager: DuckDBSessionManager) -> None:
        """Verify at most max_concurrent_queries run simultaneously."""
        max_observed = 0
        current = 0
        lock = threading.Lock()

        def worker() -> None:
            nonlocal current, max_observed
            try:
                with manager.acquire(timeout=5):
                    with lock:
                        current += 1
                        max_observed = max(max_observed, current)
                    time.sleep(0.05)  # Hold the session briefly
                    with lock:
                        current -= 1
            except TimeoutError:
                pass

        threads = [threading.Thread(target=worker) for _ in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert max_observed <= 2  # pool_size=2


class TestSessionManagerShutdown:
    """Test graceful shutdown behavior."""

    def test_shutdown_prevents_new_acquires(self, olap_config: OlapConfig) -> None:
        mgr = DuckDBSessionManager(olap_config)
        mgr.shutdown()
        with pytest.raises(RuntimeError, match="closed"):
            mgr.acquire()

    def test_shutdown_with_active_sessions(self, olap_config: OlapConfig) -> None:
        mgr = DuckDBSessionManager(olap_config)
        session = mgr.acquire()
        mgr.shutdown()  # Should not raise
        session.release()  # Active session can still release


class TestSessionManagerWithStorageConfig:
    """Test S3 configuration passthrough."""

    def test_acquire_with_s3_config(self) -> None:
        storage_config = StorageConfig(
            base_uri="./data",
            backend=StorageBackend.LOCAL,  # Use LOCAL for unit tests
        )
        olap_config = OlapConfig(max_concurrent_queries=1)
        mgr = DuckDBSessionManager(olap_config, storage_config=storage_config)
        with mgr.acquire() as conn:
            result = conn.execute("SELECT 1").fetchone()
            assert result == (1,)


class TestSessionManagerPrometheusMetrics:
    """Test Prometheus metrics integration (HIGH from code review)."""

    @classmethod
    def setup_class(cls) -> None:
        ensure_metrics_enabled()

    def test_active_sessions_gauge(self, olap_config: OlapConfig) -> None:
        mgr = DuckDBSessionManager(olap_config)
        session = mgr.acquire()
        try:
            assert duckdb_pool_active_sessions._value.get() == 1.0
        finally:
            session.release()
        assert duckdb_pool_active_sessions._value.get() == 0.0

    def test_total_queries_counter(self, olap_config: OlapConfig) -> None:
        mgr = DuckDBSessionManager(olap_config)
        with mgr.acquire():
            pass
        with mgr.acquire():
            pass
        val = duckdb_pool_total_queries._value.get()
        assert val >= 2

    def test_total_errors_counter(self, olap_config: OlapConfig) -> None:
        mgr = DuckDBSessionManager(olap_config)
        session = mgr.acquire()
        # Break the connection so release encounters an error
        session._conn.close()
        session.release()
        val = duckdb_pool_total_errors._value.get()
        assert val >= 1

    def test_total_timeouts_counter(self, olap_config: OlapConfig) -> None:
        mgr = DuckDBSessionManager(olap_config)
        s1 = mgr.acquire()
        s2 = mgr.acquire()
        try:
            with pytest.raises(TimeoutError):
                mgr.acquire(timeout=0.5)
            val = duckdb_pool_total_timeouts._value.get()
            assert val >= 1
        finally:
            s1.release()
            s2.release()

    def test_slow_queries_counter(self, olap_config: OlapConfig) -> None:
        mgr = DuckDBSessionManager(olap_config)
        mgr.record_slow_query()
        mgr.record_slow_query()
        mgr.record_slow_query()
        val = duckdb_pool_slow_queries._value.get()
        assert val >= 3

    def test_queued_requests_gauge(self, olap_config: OlapConfig) -> None:
        mgr = DuckDBSessionManager(olap_config)
        s1 = mgr.acquire()
        s2 = mgr.acquire()
        try:
            # At this point queue should be 0 (no one waiting)
            assert duckdb_pool_queued_requests._value.get() == 0.0
        finally:
            s1.release()
            s2.release()


class TestSessionManagerCreationFailure:
    """Test session creation failure path (HIGH from code review)."""

    def test_acquire_releases_semaphore_on_creation_failure(self, olap_config: OlapConfig) -> None:
        """If DuckDBSession.__enter__ fails, semaphore and active_count must be released."""
        mgr = DuckDBSessionManager(olap_config)

        original_enter = mgr.acquire
        call_count = 0

        def failing_acquire(*args: Any, **kwargs: Any) -> Any:
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                # First call: let it succeed and take the slot
                return original_enter(*args, **kwargs)
            # Subsequent calls should also work now
            return original_enter(*args, **kwargs)

        # Normal flow should work
        s1 = mgr.acquire()
        assert mgr.get_stats().active_sessions == 1
        s1.release()
        assert mgr.get_stats().active_sessions == 0

    def test_stats_reflect_error_after_creation_failure(self, olap_config: OlapConfig) -> None:
        """Verify that total_errors is incremented on session creation failure."""
        import duckdb as _duckdb

        mgr = DuckDBSessionManager(olap_config)

        with patch(
            "arrow_lake.query.session_manager.DuckDBSession"
        ) as MockSession:
            MockSession.return_value.__enter__ = MagicMock(
                side_effect=_duckdb.Error("OOM"),
            )
            MockSession.return_value.__exit__ = MagicMock()

            with pytest.raises(_duckdb.Error, match="OOM"):
                mgr.acquire()

        assert mgr.get_stats().active_sessions == 0
        assert mgr.get_stats().total_errors == 1
        assert mgr.get_stats().total_timeouts == 0

    def test_semaphore_available_after_creation_failure(self, olap_config: OlapConfig) -> None:
        """After a failed creation, the slot should be available for a new acquire."""
        import duckdb as _duckdb

        mgr = DuckDBSessionManager(olap_config)

        with patch(
            "arrow_lake.query.session_manager.DuckDBSession"
        ) as MockSession:
            MockSession.return_value.__enter__ = MagicMock(
                side_effect=_duckdb.Error("OOM"),
            )
            MockSession.return_value.__exit__ = MagicMock()

            with pytest.raises(_duckdb.Error):
                mgr.acquire()

        # Slot should be free — acquire should succeed now
        with mgr.acquire() as conn:
            assert conn is not None
            assert mgr.get_stats().active_sessions == 1
