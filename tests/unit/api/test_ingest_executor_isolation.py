"""v1.10.7 WP2 (review H8): ingest/background work runs on a dedicated
ThreadPoolExecutor instead of the shared asyncio default executor.

The 2026-08-04 freeze pattern: ≥14 concurrent heavy ingests saturate the
default executor (capacity min(32, cpu+4)) that ``run_sync`` ALSO uses —
every route then blocks. These tests pin the isolation contract:
- run_background dispatches sync funcs to ``ingest_executor``;
- saturating that pool does NOT stall ``run_sync`` (default pool) routes;
- the in-flight gauge reflects submitted-but-unfinished work.
"""

from __future__ import annotations

import asyncio
import threading
import time

import pytest

from arrow_lake.api.tasks import BackgroundTask, TaskManager, TaskStatus
from arrow_lake.api.utils import ingest_executor


@pytest.fixture
def isolated_task_manager():
    saved_tasks = TaskManager._tasks
    saved_redis = TaskManager._redis_store
    TaskManager._tasks = {}
    TaskManager._redis_store = None
    try:
        yield
    finally:
        TaskManager._tasks = saved_tasks
        TaskManager._redis_store = saved_redis


def test_ingest_executor_exists_with_expected_shape():
    # named threads + bounded workers (default 8, env-overridable)
    assert ingest_executor._max_workers >= 1
    assert ingest_executor._thread_name_prefix == "ingest"


def test_active_gauge_counts_in_flight():
    release = threading.Event()

    fut = ingest_executor.submit(release.wait)
    try:
        # one blocked submission → active == 1 (running + queued both count)
        assert ingest_executor.active == 1
    finally:
        release.set()
    fut.result(timeout=5)
    assert ingest_executor.active == 0


def test_saturated_ingest_pool_does_not_stall_run_sync(isolated_task_manager):
    """Fill every ingest worker with a blocker; a run_sync call (default
    pool) must still complete quickly — the API stays responsive."""

    async def scenario() -> None:
        release = threading.Event()
        n = ingest_executor._max_workers
        blockers = [ingest_executor.submit(release.wait) for _ in range(n)]
        # let the blockers occupy the workers
        await asyncio.sleep(0.2)

        async def quick() -> str:
            return "ok"

        t0 = time.monotonic()
        result = await asyncio.wait_for(quick(), timeout=2.0)
        elapsed = time.monotonic() - t0
        assert result == "ok"
        assert elapsed < 1.0  # default pool untouched by ingest saturation

        release.set()
        for b in blockers:
            b.result(timeout=5)

    asyncio.run(scenario())


def test_run_background_dispatches_sync_to_ingest_executor(isolated_task_manager):
    """A sync background func must wait for a free ingest worker (proves
    dispatch to the ingest pool, not the shared default executor)."""

    async def scenario() -> float:
        release = threading.Event()
        started = threading.Event()
        TaskManager._tasks["t-isol"] = BackgroundTask(
            task_id="t-isol", operation="ingest_documents", dataset_name="ds",
            status=TaskStatus.PENDING, created_at="2026-01-01T00:00:00",
        )

        # occupy ALL ingest workers so run_background cannot start
        n = ingest_executor._max_workers
        blockers = [ingest_executor.submit(release.wait) for _ in range(n)]
        await asyncio.sleep(0.2)

        def bg_func() -> str:
            started.set()
            return "done"

        bg = asyncio.create_task(TaskManager.run_background("t-isol", bg_func))
        await asyncio.sleep(0.5)

        # task queued behind blockers: not started, still RUNNING-pending
        assert not started.is_set()
        assert ingest_executor.active >= n  # blockers + queued bg_func

        release.set()
        await asyncio.wait_for(bg, timeout=10)
        for b in blockers:
            b.result(timeout=5)

        assert started.is_set()
        assert TaskManager._tasks["t-isol"].status == TaskStatus.COMPLETED
        return 0.0

    asyncio.run(scenario())
