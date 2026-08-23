"""DuckDB connection pool — Story 1.6.

Provides a semaphore-governed connection pool for DuckDB with:
- Configurable max pool size
- READ (concurrent) and WRITE (concurrent, same as READ)
- Context manager lifecycle
- PoolHealth reporting
- 30s default timeout on connection acquisition
- Parameterized query support
"""

from __future__ import annotations

import contextlib
import re
import threading
from enum import StrEnum
from typing import Any, cast

import duckdb
from pydantic import BaseModel

# Pattern for safe SQL identifiers (table names, column names)
_SAFE_IDENTIFIER_RE = re.compile(r"^[a-zA-Z_][a-zA-Z0-9_-]*$")


class PoolMode(StrEnum):
    """Connection pool access mode.

    Note: Both READ and WRITE acquire connections from the same
    semaphore-backed pool. Full read-write locking is not yet
    implemented — all connections are treated as concurrent.
    """

    READ = "read"
    WRITE = "write"


class PoolHealth(BaseModel):
    """Health report for the connection pool.

    Attributes:
        pool_size: Maximum number of connections.
        active_connections: Currently checked-out connections.
        idle_connections: Available connections in the pool.
        is_healthy: Whether the pool is operational.
    """

    pool_size: int = 0
    active_connections: int = 0
    idle_connections: int = 0
    is_healthy: bool = True


class DuckDBConnectionPool:
    """Thread-safe DuckDB connection pool.

    Uses threading.Semaphore to limit concurrent connections.

    Args:
        max_size: Maximum number of concurrent connections.
        timeout: Seconds to wait for a connection (default 30s).
        database: DuckDB connection string. Defaults to ":memory:".
            Use a file path for shared schema across connections.
    """

    def __init__(
        self,
        max_size: int = 5,
        timeout: float = 30.0,
        database: str = ":memory:",
    ) -> None:
        self.max_size = max_size
        self.timeout = timeout
        self._database = database
        self._closed = False
        self._lock = threading.Lock()
        self._semaphore = threading.Semaphore(max_size)
        self._active_count = 0

    def close(self) -> None:
        """Close the pool and release all resources."""
        with self._lock:
            self._closed = True

    def is_closed(self) -> bool:
        """Check if the pool is closed."""
        return self._closed

    def acquire(self, mode: PoolMode = PoolMode.READ, timeout: float | None = None) -> Any:
        """Acquire a DuckDB connection.

        Args:
            mode: READ or WRITE (both use the same pool).
            timeout: Override pool timeout (default: pool timeout).

        Returns:
            A DuckDB connection object.

        Raises:
            RuntimeError: If pool is closed.
            TimeoutError: If connection cannot be acquired within timeout.
        """
        if self._closed:
            raise RuntimeError("Cannot acquire from closed pool")

        effective_timeout = timeout if timeout is not None else self.timeout
        acquired = self._semaphore.acquire(timeout=effective_timeout)
        if not acquired:
            raise TimeoutError(f"Could not acquire connection within {effective_timeout}s")

        with self._lock:
            self._active_count += 1

        try:
            return duckdb.connect(self._database)
        except Exception:
            # v1.10.7 WP6 (review H11): a failed connect must give the permit
            # and the slot back — otherwise every failure permanently shrinks
            # the pool until acquire() times out forever.
            with self._lock:
                self._active_count = max(0, self._active_count - 1)
            self._semaphore.release()
            raise

    def release(self, conn: Any) -> None:
        """Release a connection back to the pool.

        Args:
            conn: The DuckDB connection to release.
        """
        with self._lock:
            self._active_count = max(0, self._active_count - 1)
        self._semaphore.release()
        with contextlib.suppress(Exception):
            conn.close()

    async def async_acquire(
        self, mode: PoolMode = PoolMode.READ, timeout: float | None = None
    ) -> Any:
        """Async version of acquire (runs sync acquire in executor)."""
        import asyncio

        return await asyncio.to_thread(self.acquire, mode, timeout)

    async def async_release(self, conn: Any) -> None:
        """Async version of release (runs sync release in executor)."""
        import asyncio

        await asyncio.to_thread(self.release, conn)

    def execute(self, query: str) -> list[tuple[object, ...]]:
        """Execute a SQL query and return materialized results.

        The connection is acquired and released automatically.

        Args:
            query: SQL query string.

        Returns:
            List of result rows.
        """
        conn = self.acquire(mode=PoolMode.READ)
        try:
            result = conn.execute(query)
            return cast(list[tuple[object, ...]], result.fetchall())
        finally:
            self.release(conn)

    def execute_params(
        self, query: str, params: tuple[object, ...] | list[object] = ()
    ) -> list[tuple[object, ...]]:
        """Execute a parameterized SQL query.

        Uses DuckDB's ``?`` placeholder syntax to prevent SQL injection.

        Args:
            query: SQL query with ``?`` placeholders.
            params: Parameter values to bind.

        Returns:
            List of result rows.
        """
        conn = self.acquire(mode=PoolMode.READ)
        try:
            result = conn.execute(query, params)
            return cast(list[tuple[object, ...]], result.fetchall())
        finally:
            self.release(conn)

    def health(self) -> PoolHealth:
        """Get the current pool health.

        Returns:
            PoolHealth snapshot of the pool state.
        """
        with self._lock:
            active = self._active_count
            idle = max(0, self.max_size - active)

        return PoolHealth(
            pool_size=self.max_size,
            active_connections=active,
            idle_connections=idle,
            is_healthy=not self._closed,
        )

    def __enter__(self) -> DuckDBConnectionPool:
        return self

    def __exit__(self, *args: object) -> None:
        self.close()
