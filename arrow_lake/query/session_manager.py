"""Unified DuckDB session manager — Phase 3 high availability.

Provides DuckDBSessionManager for:
- Centralized connection lifecycle management
- Semaphore-based concurrency control (enforces max_concurrent_queries)
- Per-connection memory limits and statement timeouts
- Idle connection recycling
- Query statistics and metrics export
- Graceful shutdown

Thread safety: all public methods are thread-safe.
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass
from typing import Any

import duckdb

from arrow_lake.config import OlapConfig, StorageConfig
from arrow_lake.core.metrics import (
    duckdb_pool_active_sessions,
    duckdb_pool_queued_requests,
    duckdb_pool_slow_queries,
    duckdb_pool_total_errors,
    duckdb_pool_total_queries,
    duckdb_pool_total_timeouts,
    get_metrics_enabled,
)
from arrow_lake.query._db import DuckDBSession

logger = logging.getLogger(__name__)

__all__ = ["DuckDBSessionManager", "SessionPoolStats"]


@dataclass(frozen=True)
class SessionPoolStats:
    """Snapshot of session manager statistics.

    Attributes:
        pool_size: Maximum concurrent sessions.
        active_sessions: Currently executing sessions.
        queued_requests: Requests waiting for a session.
        total_queries: Total queries executed since startup.
        total_errors: Total query errors since startup.
        total_timeouts: Total connection acquisition timeouts.
        avg_wait_seconds: Average wait time for session acquisition.
        slow_query_count: Queries exceeding slow_query_threshold_ms.
    """

    pool_size: int = 0
    active_sessions: int = 0
    queued_requests: int = 0
    total_queries: int = 0
    total_errors: int = 0
    total_timeouts: int = 0
    avg_wait_seconds: float = 0.0
    slow_query_count: int = 0


class _ManagedSession:
    """Wrapper that tracks session lifecycle and records metrics."""

    def __init__(
        self,
        session: DuckDBSession,
        conn: duckdb.DuckDBPyConnection,
        manager: DuckDBSessionManager,
    ) -> None:
        self._session = session
        self._conn = conn
        self._manager = manager
        self._released = False

    @property
    def conn(self) -> duckdb.DuckDBPyConnection:
        return self._conn

    def release(self) -> None:
        """Return the session to the manager."""
        if not self._released:
            self._released = True
            self._manager._release_session(self)

    def __enter__(self) -> duckdb.DuckDBPyConnection:
        return self._conn

    def __exit__(self, *args: Any) -> None:
        self.release()

    def __del__(self) -> None:
        if not self._released:
            try:
                self.release()
            except Exception:
                pass


class DuckDBSessionManager:
    """Unified DuckDB session manager with concurrency control.

    Enforces max_concurrent_queries from OlapConfig, applies per-connection
    resource governance (memory, timeout), and tracks query statistics.

    Args:
        olap_config: OLAP configuration with governance parameters.
        storage_config: Storage configuration for S3 setup.
        slow_query_threshold_ms: Log queries exceeding this threshold (default 5000).
    """

    def __init__(
        self,
        olap_config: OlapConfig,
        storage_config: StorageConfig | None = None,
        *,
        slow_query_threshold_ms: int = 5000,
    ) -> None:
        self._olap_config = olap_config
        self._storage_config = storage_config
        self._slow_query_threshold_ms = slow_query_threshold_ms

        max_queries = olap_config.max_concurrent_queries
        self._semaphore = threading.Semaphore(max_queries)
        self._lock = threading.Lock()

        # Statistics
        self._active_count = 0
        self._queued_count = 0
        self._total_queries = 0
        self._total_errors = 0
        self._total_timeouts = 0
        self._total_wait_time = 0.0
        self._slow_query_count = 0

        self._closed = False

    @property
    def pool_size(self) -> int:
        return self._olap_config.max_concurrent_queries

    def acquire(
        self,
        *,
        timeout: float | None = None,
        load_ducklake: bool = False,
    ) -> _ManagedSession:
        """Acquire a managed DuckDB session.

        Blocks until a session is available or timeout is reached.

        Args:
            timeout: Max wait time in seconds (None = no wait).
            load_ducklake: Whether to load the ducklake extension.

        Returns:
            _ManagedSession context manager wrapping a DuckDB connection.

        Raises:
            TimeoutError: If a session cannot be acquired within timeout.
            RuntimeError: If the manager is closed.
        """
        if self._closed:
            raise RuntimeError("Cannot acquire from closed session manager")

        effective_timeout = timeout if timeout is not None else self._olap_config.query_timeout_seconds

        # Track queued requests
        with self._lock:
            self._queued_count += 1
            if get_metrics_enabled():
                duckdb_pool_queued_requests.set(self._queued_count)

        t_wait_start = time.monotonic()
        acquired = self._semaphore.acquire(timeout=effective_timeout)
        wait_time = time.monotonic() - t_wait_start

        with self._lock:
            self._queued_count = max(0, self._queued_count - 1)
            self._total_wait_time += wait_time
            if get_metrics_enabled():
                duckdb_pool_queued_requests.set(self._queued_count)

        if not acquired:
            with self._lock:
                self._total_timeouts += 1
                if get_metrics_enabled():
                    duckdb_pool_total_timeouts.inc()
            raise TimeoutError(
                f"Could not acquire DuckDB session within {effective_timeout}s "
                f"(pool_size={self.pool_size}, active={self._active_count})"
            )

        with self._lock:
            self._active_count += 1
            self._total_queries += 1
            if get_metrics_enabled():
                duckdb_pool_active_sessions.set(self._active_count)
                duckdb_pool_total_queries.inc()

        try:
            session = DuckDBSession(
                max_memory_mb=self._olap_config.max_query_memory_mb,
                timeout_seconds=self._olap_config.query_timeout_seconds,
                load_ducklake=load_ducklake,
                olap_config=self._olap_config,
                storage_config=self._storage_config,
            )
            conn = session.__enter__()
        except duckdb.Error:
            self._semaphore.release()
            with self._lock:
                self._active_count -= 1
                self._total_errors += 1
                if get_metrics_enabled():
                    duckdb_pool_active_sessions.set(self._active_count)
                    duckdb_pool_total_errors.inc()
            raise

        return _ManagedSession(session, conn, self)

    def _release_session(self, managed: _ManagedSession) -> None:
        """Release a managed session back to the pool."""
        try:
            managed._session.__exit__(None, None, None)
        except duckdb.Error:
            with self._lock:
                self._total_errors += 1
                if get_metrics_enabled():
                    duckdb_pool_total_errors.inc()
            logger.warning("Error closing DuckDB session", exc_info=True)
        finally:
            with self._lock:
                self._active_count = max(0, self._active_count - 1)
                if get_metrics_enabled():
                    duckdb_pool_active_sessions.set(self._active_count)
            self._semaphore.release()

    def record_slow_query(self) -> None:
        """Record a slow query event (called by query bridges)."""
        with self._lock:
            self._slow_query_count += 1
            if get_metrics_enabled():
                duckdb_pool_slow_queries.inc()

    def get_stats(self) -> SessionPoolStats:
        """Get current session pool statistics.

        Returns:
            SessionPoolStats snapshot.
        """
        with self._lock:
            total = self._total_queries
            total_wait = self._total_wait_time
            return SessionPoolStats(
                pool_size=self.pool_size,
                active_sessions=self._active_count,
                queued_requests=self._queued_count,
                total_queries=total,
                total_errors=self._total_errors,
                total_timeouts=self._total_timeouts,
                avg_wait_seconds=(total_wait / total) if total > 0 else 0.0,
                slow_query_count=self._slow_query_count,
            )

    def shutdown(self) -> None:
        """Gracefully shut down the session manager.

        Does not force-close active sessions — they will complete naturally.
        Prevents new session acquisitions.
        """
        with self._lock:
            self._closed = True
        logger.info(
            "Session manager shut down: queries=%d, errors=%d, timeouts=%d, slow=%d",
            self._total_queries,
            self._total_errors,
            self._total_timeouts,
            self._slow_query_count,
        )
