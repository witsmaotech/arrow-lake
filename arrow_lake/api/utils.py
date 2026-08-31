"""Shared utilities for API layer."""

from __future__ import annotations

import asyncio
import os
import threading
from concurrent.futures import ThreadPoolExecutor
from functools import partial
from typing import Any

import structlog

_log = structlog.get_logger(__name__)

_DEFAULT_TIMEOUT = 300


class CountingThreadPoolExecutor(ThreadPoolExecutor):
    """ThreadPoolExecutor tracking in-flight submissions (v1.10.7 WP2).

    ``active`` counts submitted-but-not-yet-completed work items (running +
    queued) — the pressure signal exported as a Prometheus gauge. CPython has
    no public accessor for this, hence the submit/done_callback pair.
    """

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        self._in_flight = 0
        self._count_lock = threading.Lock()
        super().__init__(*args, **kwargs)

    def submit(self, fn: Any, /, *args: Any, **kwargs: Any) -> Any:  # type: ignore[override]
        fut = super().submit(fn, *args, **kwargs)
        with self._count_lock:
            self._in_flight += 1
        fut.add_done_callback(self._mark_done)
        return fut

    def _mark_done(self, _fut: Any) -> None:
        with self._count_lock:
            self._in_flight -= 1

    @property
    def active(self) -> int:
        with self._count_lock:
            return self._in_flight


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

# Dedicated pool for async ingest / background task work (v1.10.7 WP2, review
# H8). run_background used to dispatch sync funcs to the asyncio default
# executor — the same pool run_sync uses — so ≥14 concurrent heavy ingests
# starved every route (2026-08-04 freeze pattern's residual entry point).
# Isolating here means heavy ingest only queues behind other ingest.
# Worker count via API_INGEST_WORKERS (default 8).
ingest_executor = CountingThreadPoolExecutor(
    max_workers=max(1, int(os.environ.get("API_INGEST_WORKERS", "8"))),
    thread_name_prefix="ingest",
)

# Dedicated pool for auth-plane Redis IO (M-8, v1.10.7 review 发版前清偿):
# 限流/登录的 Redis 调用原先走 run_sync 默认池——Redis 慢/挂时每个调用
# 以 5s 弃线程占位,~3 req/s/worker 即饱和该池,登录面雪崩放大到全站。
# 独立 4 线程小池把饱和半径收敛到 auth 面(olap/ingest 隔离同款纪律)。
auth_io_executor = ThreadPoolExecutor(
    max_workers=4, thread_name_prefix="auth-io",
)


def _wire_ingest_executor_gauge() -> None:
    """Export in-flight ingest work as arrow_lake_ingest_executor_active_threads."""
    try:
        from arrow_lake.core.metrics import ingest_executor_active_threads

        ingest_executor_active_threads.set_function(lambda: ingest_executor.active)
    except Exception:
        _log.debug("ingest_executor_gauge_unavailable")


_wire_ingest_executor_gauge()


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
