"""Tests for async query execution module.

M0a Day 4 — TDD for thread pool + semaphore DuckDB async bridge.
"""

from __future__ import annotations

import asyncio

import pytest


class TestRunDuckdbQuery:
    """Test run_duckdb_query async wrapper."""

    @pytest.mark.asyncio
    async def test_runs_sync_function_in_thread(self) -> None:
        """run_duckdb_query should execute a sync function and return its result."""
        from arrow_lake.query._async import run_duckdb_query

        def sync_add(a: int, b: int) -> int:
            return a + b

        result = await run_duckdb_query(sync_add, 3, 4)
        assert result == 7

    @pytest.mark.asyncio
    async def test_runs_with_kwargs(self) -> None:
        """run_duckdb_query should pass kwargs to the function."""
        from arrow_lake.query._async import run_duckdb_query

        def greet(name: str, greeting: str = "hello") -> str:
            return f"{greeting}, {name}"

        result = await run_duckdb_query(greet, "world", greeting="hi")
        assert result == "hi, world"

    @pytest.mark.asyncio
    async def test_propagates_exception(self) -> None:
        """run_duckdb_query should propagate exceptions from the sync function."""
        from arrow_lake.query._async import run_duckdb_query

        def failing() -> None:
            raise ValueError("sync error")

        with pytest.raises(ValueError, match="sync error"):
            await run_duckdb_query(failing)


class TestConcurrencyLimit:
    """Test that semaphore limits concurrent DuckDB queries."""

    @pytest.mark.asyncio
    async def test_semaphore_limits_concurrency(self) -> None:
        """Should not exceed max concurrent queries."""
        from arrow_lake.query._async import run_duckdb_query, _query_semaphore

        # Reset semaphore for test
        import arrow_lake.query._async as async_mod
        original = async_mod._query_semaphore
        async_mod._query_semaphore = asyncio.Semaphore(2)

        running = 0
        max_running = 0
        lock = asyncio.Lock()

        def tracked_task() -> int:
            nonlocal running, max_running
            import time
            # Acquire is safe in sync context within the same event loop thread
            running += 1
            if running > max_running:
                max_running = running
            time.sleep(0.05)
            running -= 1
            return 1

        tasks = [run_duckdb_query(tracked_task) for _ in range(6)]
        await asyncio.gather(*tasks)

        assert max_running <= 2

        # Restore original
        async_mod._query_semaphore = original


class TestShutdown:
    """Test graceful shutdown of query executor."""

    @pytest.mark.asyncio
    async def test_shutdown(self) -> None:
        """shutdown_query_executor should clean up the executor."""
        import arrow_lake.query._async as async_mod

        await async_mod.shutdown_query_executor()
        assert async_mod._query_executor is None
