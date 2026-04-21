"""Async query execution — prevents DuckDB from blocking the event loop.

DuckDB's native calls are synchronous and will block the FastAPI event loop.
This module provides a ThreadPoolExecutor-based bridge so DuckDB queries
run in a worker thread while the async event loop remains responsive.

Usage::

    from arrow_lake.query._async import run_duckdb_query

    # In an async context (FastAPI endpoint):
    result = await run_duckdb_query(conn.execute, "SELECT * FROM t WHERE id = 1")
"""

from __future__ import annotations

import asyncio
import logging
from concurrent.futures import ThreadPoolExecutor

logger = logging.getLogger(__name__)

__all__ = ["run_duckdb_query", "shutdown_query_executor"]

_query_executor: ThreadPoolExecutor | None = None
_query_semaphore: asyncio.Semaphore | None = None


def _get_executor(max_workers: int = 4) -> ThreadPoolExecutor:
    """Get or create the thread pool executor for DuckDB queries."""
    global _query_executor
    if _query_executor is None or _query_executor._shutdown:
        _query_executor = ThreadPoolExecutor(
            max_workers=max_workers,
            thread_name_prefix="duckdb-query",
        )
    return _query_executor


async def run_duckdb_query(func: object, *args: object, **kwargs: object) -> object:
    """Run a synchronous DuckDB function in a worker thread.

    Uses a semaphore to limit concurrent DuckDB queries (default 4).

    Args:
        func: Synchronous function to execute (e.g. conn.execute).
        *args: Positional arguments to pass to func.
        **kwargs: Keyword arguments to pass to func.

    Returns:
        The return value of func.

    Example::

        conn = duckdb.connect()
        result = await run_duckdb_query(conn.execute, "SELECT * FROM t LIMIT 10")
    """
    global _query_semaphore

    if _query_semaphore is None:
        _query_semaphore = asyncio.Semaphore(4)

    loop = asyncio.get_running_loop()
    executor = _get_executor()

    async with _query_semaphore:
        return await loop.run_in_executor(
            executor,
            lambda: func(*args, **kwargs),
        )


async def shutdown_query_executor() -> None:
    """Gracefully shut down the query thread pool.

    Should be called during application shutdown (lifespan shutdown).
    """
    global _query_executor, _query_semaphore
    if _query_executor is not None:
        _query_executor.shutdown(wait=True)
        _query_executor = None
    _query_semaphore = None
