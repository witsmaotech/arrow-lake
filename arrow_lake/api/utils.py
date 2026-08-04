"""Shared utilities for API layer."""

from __future__ import annotations

import asyncio
import os
from concurrent.futures import ThreadPoolExecutor
from functools import partial
from typing import Any

import structlog

_log = structlog.get_logger(__name__)

_DEFAULT_TIMEOUT = 300

# Dedicated pool for OLAP/DuckDB queries. A slow/blocked scan (e.g. the native
# lance scanner stalled in D-state IO) cannot be interrupted from Python — the
# worker thread stays alive until the process restarts. On the shared default
# executor that starves ingest/embed/quality/lineage handlers and freezes the
# whole API (2026-08-04 outage). This pool isolates OLAP so a stuck scan only
# ever blocks other OLAP queries. Actual DuckDB concurrency remains bounded by
# DuckDBSessionManager's semaphore (max_concurrent_queries); the pool just
# isolates OLAP from non-OLAP routes.
olap_executor = ThreadPoolExecutor(
    max_workers=max(4, os.cpu_count() or 4),
    thread_name_prefix="olap",
)


async def run_sync(
    func: Any,
    *args: Any,
    timeout: float = _DEFAULT_TIMEOUT,
    label: str = "",
    executor: Any | None = None,
    **kwargs: Any,
) -> Any:
    loop = asyncio.get_running_loop()
    coro = loop.run_in_executor(executor, partial(func, *args, **kwargs))
    try:
        return await asyncio.wait_for(coro, timeout=timeout)
    except TimeoutError:
        name = label or getattr(func, "__name__", str(func))
        _log.warning("run_sync_timeout", name=name, timeout=timeout)
        raise
