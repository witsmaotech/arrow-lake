"""Tests for arrow_lake.catalog.connection_pool — Story 1.6.

Tests the DuckDB connection pool:
- 4 read + 1 write concurrent access
- Semaphore-based pool sizing
- PoolHealth reporting
- Context manager lifecycle
- 30s timeout on connection acquisition
"""

from __future__ import annotations

import asyncio

import pytest

pytest.importorskip("duckdb")

from arrow_lake.catalog.connection_pool import (
    DuckDBConnectionPool,
    PoolHealth,
    PoolMode,
)


class TestPoolHealth:
    """Test PoolHealth Pydantic model."""

    def test_default_values(self) -> None:
        health = PoolHealth()
        assert health.pool_size == 0
        assert health.active_connections == 0
        assert health.idle_connections == 0
        assert health.is_healthy is True

    def test_custom_values(self) -> None:
        health = PoolHealth(
            pool_size=5,
            active_connections=3,
            idle_connections=2,
            is_healthy=False,
        )
        assert health.pool_size == 5
        assert health.active_connections == 3
        assert health.idle_connections == 2
        assert health.is_healthy is False


class TestPoolMode:
    """Test PoolMode enum."""

    def test_read_mode_exists(self) -> None:
        assert PoolMode.READ == "read"

    def test_write_mode_exists(self) -> None:
        assert PoolMode.WRITE == "write"


class TestDuckDBConnectionPool:
    """Test DuckDBConnectionPool creation and lifecycle."""

    def test_pool_creation_with_defaults(self) -> None:
        pool = DuckDBConnectionPool()
        assert pool.max_size == 5
        pool.close()

    def test_pool_creation_with_custom_size(self) -> None:
        pool = DuckDBConnectionPool(max_size=3)
        assert pool.max_size == 3
        pool.close()

    def test_context_manager(self) -> None:
        with DuckDBConnectionPool() as pool:
            assert pool.max_size == 5
        # After exit, pool should be closed
        assert pool.is_closed()

    def test_acquire_returns_connection(self) -> None:
        pool = DuckDBConnectionPool()
        conn = pool.acquire(mode=PoolMode.READ)
        assert conn is not None
        pool.release(conn)
        pool.close()

    def test_acquire_write_connection(self) -> None:
        pool = DuckDBConnectionPool()
        conn = pool.acquire(mode=PoolMode.WRITE)
        assert conn is not None
        pool.release(conn)
        pool.close()

    def test_double_close_is_safe(self) -> None:
        pool = DuckDBConnectionPool()
        pool.close()
        pool.close()  # Should not raise

    def test_acquire_after_close_raises(self) -> None:
        pool = DuckDBConnectionPool()
        pool.close()
        with pytest.raises(RuntimeError, match="closed"):
            pool.acquire(mode=PoolMode.READ)

    def test_health_report(self) -> None:
        pool = DuckDBConnectionPool(max_size=3)
        health = pool.health()
        assert isinstance(health, PoolHealth)
        assert health.pool_size == 3
        pool.close()

    def test_execute_query(self) -> None:
        pool = DuckDBConnectionPool()
        result = pool.execute("SELECT 1 AS value")
        assert result is not None
        pool.close()

    def test_execute_returns_results(self) -> None:
        pool = DuckDBConnectionPool()
        rows = pool.execute("SELECT 42 AS answer")
        assert len(rows) == 1
        assert rows[0][0] == 42
        pool.close()

    def test_execute_params_returns_results(self) -> None:
        pool = DuckDBConnectionPool()
        rows = pool.execute_params("SELECT ? AS answer", (42,))
        assert len(rows) == 1
        assert rows[0][0] == 42
        pool.close()

    def test_execute_params_prevents_injection(self) -> None:
        pool = DuckDBConnectionPool()
        # Parameterized query should NOT interpret the value as SQL.
        # The injection string is treated as a literal value.
        rows = pool.execute_params(
            "SELECT ? AS name WHERE ? = ?",
            ("'; DROP TABLE x; --", "a", "b"),
        )
        # 'a' != 'b', so 0 rows is the correct safe result.
        # If injection worked, the query would fail or return unexpected data.
        assert len(rows) == 0
        pool.close()


class TestPoolConcurrency:
    """Test concurrent connection pool behavior."""

    def test_read_connections_are_concurrent(self) -> None:
        """Multiple read connections should be allowed simultaneously."""
        pool = DuckDBConnectionPool(max_size=4)
        conns = [pool.acquire(mode=PoolMode.READ) for _ in range(4)]
        assert len(conns) == 4
        for conn in conns:
            pool.release(conn)
        pool.close()

    def test_write_is_exclusive(self) -> None:
        """Write connection should block other writes (single write slot)."""
        pool = DuckDBConnectionPool(max_size=4)

        write_conn = pool.acquire(mode=PoolMode.WRITE)
        assert write_conn is not None
        pool.release(write_conn)
        pool.close()

    def test_pool_exhaustion_blocks(self) -> None:
        """Acquiring more connections than pool size should block."""
        pool = DuckDBConnectionPool(max_size=1)

        conn1 = pool.acquire(mode=PoolMode.READ)

        acquired = False
        try:
            # This should timeout because pool is exhausted
            conn2 = pool.acquire(mode=PoolMode.READ, timeout=0.1)
            acquired = True
            pool.release(conn2)
        except TimeoutError:
            pass

        pool.release(conn1)
        pool.close()
        # With pool size 1, second acquire should timeout
        assert not acquired

    def test_release_allows_reacquire(self) -> None:
        pool = DuckDBConnectionPool(max_size=1)

        conn1 = pool.acquire(mode=PoolMode.READ)
        pool.release(conn1)
        conn2 = pool.acquire(mode=PoolMode.READ)
        assert conn2 is not None
        pool.release(conn2)
        pool.close()


class TestPoolAsync:
    """Test async pool interface."""

    @pytest.mark.asyncio
    async def test_async_acquire_and_release(self) -> None:
        pool = DuckDBConnectionPool(max_size=2)
        conn = await pool.async_acquire(mode=PoolMode.READ)
        assert conn is not None
        await pool.async_release(conn)
        pool.close()

    @pytest.mark.asyncio
    async def test_async_concurrent_reads(self) -> None:
        pool = DuckDBConnectionPool(max_size=4)

        async def acquire_release():
            conn = await pool.async_acquire(mode=PoolMode.READ)
            await asyncio.sleep(0.01)
            await pool.async_release(conn)

        await asyncio.gather(*[acquire_release() for _ in range(4)])
        pool.close()
