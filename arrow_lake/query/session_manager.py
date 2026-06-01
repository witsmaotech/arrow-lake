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

import contextlib
import logging
import os
import threading
import time
import weakref
from collections import deque
from dataclasses import dataclass, field
from typing import Any

import duckdb

from arrow_lake.config import OlapConfig, RedisConfig, StorageConfig
from arrow_lake.core.metrics import (
    duckdb_memory_budget_mb,
    duckdb_pool_active_sessions,
    duckdb_pool_evicted_connections_total,
    duckdb_pool_health_checks_total,
    duckdb_pool_queued_requests,
    duckdb_pool_slow_queries,
    duckdb_pool_total_errors,
    duckdb_pool_total_queries,
    duckdb_pool_total_timeouts,
    duckdb_pool_warmup_errors_total,
    duckdb_pool_warmup_total,
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
        instance_count: Number of DuckDB instances in the cluster (1 = standalone).
        total_capacity: Total capacity across all instances (instance_count * pool_size).
    """

    pool_size: int = 0
    active_sessions: int = 0
    queued_requests: int = 0
    total_queries: int = 0
    total_errors: int = 0
    total_timeouts: int = 0
    avg_wait_seconds: float = 0.0
    slow_query_count: int = 0
    instance_count: int = 1
    total_capacity: int = 0


class _ManagedSession:
    """Wrapper that tracks session lifecycle and records metrics."""

    def __init__(
        self,
        session: DuckDBSession,
        conn: duckdb.DuckDBPyConnection,
        manager: DuckDBSessionManager,
        created_at: float = 0.0,
    ) -> None:
        self._session = session
        self._conn = conn
        self._manager = manager
        self._released = False
        self._created_at = created_at or time.monotonic()
        self._finalizer = weakref.finalize(
            self, type(self)._cleanup, self._manager, self._conn, self._created_at
        )

    @property
    def conn(self) -> duckdb.DuckDBPyConnection:
        return self._conn

    def release(self) -> None:
        """Return the session to the manager."""
        if not self._released:
            self._released = True
            self._finalizer.detach()
            self._manager._release_session(self)

    def __enter__(self) -> duckdb.DuckDBPyConnection:
        return self._conn

    def __exit__(self, *args: Any) -> None:
        self.release()

    @staticmethod
    def _cleanup(manager: Any, conn: Any, created_at: float) -> None:
        with contextlib.suppress(Exception):
            manager._return_or_close(conn, manager._health_check(conn) if conn else False, created_at)


@dataclass(frozen=True)
class _IdleConnection:
    """A connection sitting in the idle pool, awaiting reuse."""

    conn: duckdb.DuckDBPyConnection
    returned_at: float = field(default_factory=time.monotonic)
    created_at: float = field(default_factory=time.monotonic)
    configured_memory_mb: int = 0
    configured_threads: int = 0
    configured_timeout: float = 0.0


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
        semaphore: threading.Semaphore | Any | None = None,
        slow_query_threshold_ms: int = 5000,
        idle_timeout_seconds: int = 300,
        max_session_lifetime_seconds: int = 3600,
        instance_count: int = 1,
        **kwargs: Any,
    ) -> None:
        self._olap_config = olap_config
        self._storage_config = storage_config
        self._slow_query_threshold_ms = slow_query_threshold_ms
        self._idle_timeout_seconds = idle_timeout_seconds
        self._max_session_lifetime_seconds = max_session_lifetime_seconds
        self._instance_count = max(1, instance_count)
        self._instance_registry: Any = kwargs.get("_instance_registry")

        max_queries = olap_config.max_concurrent_queries
        self._semaphore = semaphore or threading.Semaphore(max_queries)
        self._lock = threading.Lock()

        # Idle connection pool
        self._idle_pool: deque[_IdleConnection] = deque()
        self._max_idle = max_queries
        self._conn_sessions: dict[int, DuckDBSession] = {}  # id(conn) → session

        # Statistics
        self._active_count = 0
        self._queued_count = 0
        self._total_queries = 0
        self._total_errors = 0
        self._total_timeouts = 0
        self._total_wait_time = 0.0
        self._slow_query_count = 0

        self._closed = False

        # Record memory budget metric
        if get_metrics_enabled():
            duckdb_memory_budget_mb.set(olap_config.memory_budget_mb())

    @classmethod
    def from_config(
        cls,
        olap_config: OlapConfig,
        storage_config: StorageConfig | None = None,
        redis_config: RedisConfig | None = None,
        **kwargs: Any,
    ) -> DuckDBSessionManager:
        """Factory method: create a manager with the appropriate semaphore.

        When *redis_config* is provided and enabled, uses a distributed
        Redis-backed semaphore and registers the instance in the cluster
        registry; otherwise falls back to threading.Semaphore.
        """
        from arrow_lake.query._redis_semaphore import (
            create_instance_registry,
            create_semaphore,
        )

        semaphore = create_semaphore(redis_config, olap_config.max_concurrent_queries)
        registry = create_instance_registry(redis_config)

        if registry is not None:
            registry.register()
            kwargs.setdefault("instance_count", registry.discover_instance_count())
            kwargs["_instance_registry"] = registry

        return cls(
            olap_config=olap_config,
            storage_config=storage_config,
            semaphore=semaphore,
            **kwargs,
        )

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
            conn, created_at = self._acquire_connection(load_ducklake)
        except duckdb.Error:
            self._semaphore.release()
            with self._lock:
                self._active_count -= 1
                self._total_errors += 1
                if get_metrics_enabled():
                    duckdb_pool_active_sessions.set(self._active_count)
                    duckdb_pool_total_errors.inc()
            raise

        return _ManagedSession(None, conn, self, created_at=created_at)

    def _release_session(self, managed: _ManagedSession) -> None:
        """Release a managed session — return to idle pool or destroy."""
        healthy = self._health_check(managed._conn)
        self._return_or_close(managed._conn, healthy, managed._created_at)

    def _return_or_close(
        self,
        conn: duckdb.DuckDBPyConnection,
        healthy: bool,
        created_at: float,
    ) -> None:
        """Return connection to idle pool or close it."""
        if healthy:
            with self._lock:
                if len(self._idle_pool) < self._max_idle:
                    self._idle_pool.append(_IdleConnection(
                        conn=conn,
                        created_at=created_at,
                        configured_memory_mb=self._olap_config.max_query_memory_mb,
                        configured_threads=os.cpu_count() or 4,
                        configured_timeout=self._olap_config.query_timeout_seconds,
                    ))
                else:
                    self._close_conn(conn)
        else:
            self._close_conn(conn)

        with self._lock:
            self._active_count = max(0, self._active_count - 1)
            if get_metrics_enabled():
                duckdb_pool_active_sessions.set(self._active_count)
        self._semaphore.release()

    def _acquire_connection(
        self, load_ducklake: bool,
    ) -> tuple[duckdb.DuckDBPyConnection, float]:
        """Get a connection from idle pool or create a new one.

        Returns:
            Tuple of (connection, created_at_timestamp).
        """
        # Try idle pool first
        while True:
            with self._lock:
                if not self._idle_pool:
                    break
                idle = self._idle_pool.popleft()
                age = time.monotonic() - idle.returned_at
                lifetime = time.monotonic() - idle.created_at
                if age > self._idle_timeout_seconds or lifetime > self._max_session_lifetime_seconds:
                    self._close_conn(idle.conn)
                    if get_metrics_enabled():
                        duckdb_pool_evicted_connections_total.inc()
                    continue
                conn = idle.conn
                created_at = idle.created_at
                config_changed = (
                    idle.configured_memory_mb != self._olap_config.max_query_memory_mb
                    or idle.configured_threads != (os.cpu_count() or 4)
                    or idle.configured_timeout != self._olap_config.query_timeout_seconds
                )

            if self._health_check(conn):
                try:
                    if config_changed:
                        conn.execute("RESET memory_limit")
                        conn.execute("RESET threads")
                        conn.execute(f"SET memory_limit='{int(self._olap_config.max_query_memory_mb)}MB';")
                        conn.execute(f"SET threads={os.cpu_count() or 4};")
                        with contextlib.suppress(duckdb.CatalogException):
                            conn.execute(f"SET statement_timeout='{int(self._olap_config.query_timeout_seconds)}s';")
                        # Re-apply performance tuning
                        if not self._olap_config.preserve_insertion_order:
                            conn.execute("SET preserve_insertion_order = false;")
                        if self._olap_config.temp_directory:
                            conn.execute(f"SET temp_directory = '{self._olap_config.temp_directory}';")
                        if self._olap_config.enable_progress_bar:
                            conn.execute("SET enable_progress_bar = true;")
                            conn.execute("SET progress_bar_time = 2000;")
                        if self._olap_config.enable_profiling:
                            with contextlib.suppress(duckdb.CatalogException):
                                conn.execute("SET profiling_mode = 'detailed';")
                    if load_ducklake:
                        conn.execute("INSTALL ducklake; LOAD ducklake;")
                except duckdb.Error:
                    self._close_conn(conn)
                    continue
                return conn, created_at
            else:
                self._close_conn(conn)

        # No usable idle connection — create new (retry once on failure)
        for attempt in range(2):
            try:
                session = DuckDBSession(
                    max_memory_mb=self._olap_config.max_query_memory_mb,
                    timeout_seconds=self._olap_config.query_timeout_seconds,
                    load_ducklake=load_ducklake,
                    olap_config=self._olap_config,
                    storage_config=self._storage_config,
                )
                conn = session.__enter__()
                self._conn_sessions[id(conn)] = session
                return conn, time.monotonic()
            except duckdb.Error:
                session.__exit__(None, None, None)
                if attempt == 0:
                    logger.warning("connection_creation_failed_retrying")
                    continue
                raise

    def _health_check(self, conn: duckdb.DuckDBPyConnection) -> bool:
        """Check if a connection is still alive."""
        try:
            conn.execute("SELECT 1").fetchone()
            if get_metrics_enabled():
                duckdb_pool_health_checks_total.inc()
            return True
        except duckdb.Error:
            with self._lock:
                self._total_errors += 1
                if get_metrics_enabled():
                    duckdb_pool_total_errors.inc()
            return False

    def _close_conn(self, conn: duckdb.DuckDBPyConnection) -> None:
        """Close a DuckDB connection and restore S3 env vars if applicable."""
        session = self._conn_sessions.pop(id(conn), None)
        if session is not None:
            with contextlib.suppress(Exception):
                session.__exit__(None, None, None)
            return
        try:
            conn.close()
        except duckdb.Error as exc:
            logger.warning("Error closing DuckDB connection: %s", exc)

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
        instance_count = self._resolve_instance_count()
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
                instance_count=instance_count,
                total_capacity=instance_count * self.pool_size,
            )

    def shutdown(self) -> None:
        """Gracefully shut down the session manager.

        Drains the idle pool, deregisters from the cluster, and prevents
        new acquisitions.  Active sessions will complete naturally.
        """
        with self._lock:
            self._closed = True
            idle_conns = list(self._idle_pool)
            self._idle_pool.clear()

        for idle in idle_conns:
            self._close_conn(idle.conn)

        # Deregister from cluster registry
        if self._instance_registry is not None:
            self._instance_registry.shutdown()
            self._instance_registry = None

        logger.info(
            "Session manager shut down: queries=%d, errors=%d, timeouts=%d, slow=%d, idle_drained=%d",
            self._total_queries,
            self._total_errors,
            self._total_timeouts,
            self._slow_query_count,
            len(idle_conns),
        )

    def warmup(self, count: int | None = None) -> dict[str, Any]:
        """Pre-create connections for cold-start optimization.

        Args:
            count: Number of connections to pre-create. Defaults to the
                value from OlapConfig.warmup_connections (usually 2).

        Returns:
            Dict with warmup results: ``{"warmed": int, "errors": int}``.
        """
        if count is None:
            count = min(self._olap_config.warmup_connections, self.pool_size)

        warmed = 0
        errors = 0
        for _ in range(count):
            try:
                managed = self.acquire()
                try:
                    managed.conn.execute("SELECT 1")
                finally:
                    managed.release()
                warmed += 1
                if get_metrics_enabled():
                    duckdb_pool_warmup_total.inc()
            except Exception as exc:
                errors += 1
                if get_metrics_enabled():
                    duckdb_pool_warmup_errors_total.inc()
                logger.warning("warmup_connection_failed: %s", exc)

        logger.info("Warmed up %d/%d DuckDB connection(s)", warmed, count)
        return {"warmed": warmed, "errors": errors}

    def _resolve_instance_count(self) -> int:
        """Resolve current cluster instance count from registry or local config."""
        if self._instance_registry is not None:
            try:
                return self._instance_registry.discover_instance_count()
            except Exception:
                pass
        return self._instance_count
