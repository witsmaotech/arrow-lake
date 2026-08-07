"""Tests for TaskManager.reap_orphaned_tasks — the orphaned-task reaper.

Covers the 2026-08-07 outage: when a worker dies (dev --reload, gunicorn
recycle/timeout, OOM, crash) mid-task, run_background's finally never runs and
the task is stranded in running forever, blocking the console's ingest de-dup
guard. The startup reaper reclaims such tasks using the heartbeat / created_at.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

import pytest

from arrow_lake.api.tasks import BackgroundTask, TaskManager, TaskStatus


@pytest.fixture
def isolated_task_manager():
    """Snapshot TaskManager ClassVar state so tests don't pollute each other."""
    saved_tasks = TaskManager._tasks
    saved_redis = TaskManager._redis_store
    TaskManager._tasks = {}
    TaskManager._redis_store = None  # in-memory only
    try:
        yield
    finally:
        TaskManager._tasks = saved_tasks
        TaskManager._redis_store = saved_redis


def _age_task(task_id: str, status: TaskStatus, *, hours_old: float, heartbeat_hours: float | None) -> str:
    """Create a task in TaskManager._tasks with a back-dated created_at/heartbeat."""
    created = datetime.now(UTC) - timedelta(hours=hours_old)
    t = BackgroundTask(
        task_id=task_id, operation="ingest_documents", dataset_name="ds",
        status=status, created_at=created.isoformat(),
    )
    if heartbeat_hours is not None:
        t.detail["heartbeat"] = (datetime.now(UTC) - timedelta(hours=heartbeat_hours)).isoformat()
    TaskManager._tasks[task_id] = t
    return task_id


def test_reaps_stale_running_task_without_heartbeat(isolated_task_manager):
    # Arrange — legacy orphan: running, no heartbeat, created long ago.
    _age_task("stale1", TaskStatus.RUNNING, hours_old=2, heartbeat_hours=None)

    # Act
    reaped = TaskManager.reap_orphaned_tasks()

    # Assert
    assert reaped == 1
    t = TaskManager._tasks["stale1"]
    assert t.status == TaskStatus.FAILED
    assert "orphaned" in (t.error or "")
    assert t.completed_at is not None


def test_spare_fresh_running_task(isolated_task_manager):
    # Arrange — just started (created < stale_seconds ago).
    _age_task("fresh", TaskStatus.RUNNING, hours_old=0.0, heartbeat_hours=None)
    TaskManager._tasks["fresh"].created_at = datetime.now(UTC).isoformat()

    # Act
    reaped = TaskManager.reap_orphaned_tasks()

    # Assert — spared (age guard).
    assert reaped == 0
    assert TaskManager._tasks["fresh"].status == TaskStatus.RUNNING


def test_spare_task_with_recent_heartbeat(isolated_task_manager):
    # Arrange — old created_at but a live worker keeps heartbeating.
    _age_task("live", TaskStatus.RUNNING, hours_old=2, heartbeat_hours=0.0)
    TaskManager._tasks["live"].detail["heartbeat"] = datetime.now(UTC).isoformat()

    # Act
    reaped = TaskManager.reap_orphaned_tasks()

    # Assert — heartbeat is fresh, so NOT orphaned.
    assert reaped == 0
    assert TaskManager._tasks["live"].status == TaskStatus.RUNNING


def test_reaps_task_with_stale_heartbeat(isolated_task_manager):
    # Arrange — created recently but the worker died (heartbeat went stale).
    _age_task("dead", TaskStatus.RUNNING, hours_old=0.0, heartbeat_hours=2)

    # Act
    reaped = TaskManager.reap_orphaned_tasks()

    # Assert
    assert reaped == 1
    assert TaskManager._tasks["dead"].status == TaskStatus.FAILED


def test_does_not_touch_terminal_tasks(isolated_task_manager):
    # Arrange — completed/failed tasks are never reaped.
    _age_task("done", TaskStatus.COMPLETED, hours_old=5, heartbeat_hours=None)

    # Act
    reaped = TaskManager.reap_orphaned_tasks()

    # Assert
    assert reaped == 0
    assert TaskManager._tasks["done"].status == TaskStatus.COMPLETED


def test_run_background_marks_failed_when_a_step_hangs(monkeypatch, isolated_task_manager):
    """A hung critical operation must still transition the task to FAILED.

    This is the root-cause fix: ``run_background`` wraps the executor call in
    wait_for so a step that stalls without raising (socket hang, docling stuck)
    can't strand the task in running forever.
    """
    # Arrange — shrink the lifetime ceiling so the test is fast.
    monkeypatch.setattr("arrow_lake.api.tasks._TASK_MAX_LIFETIME_SECONDS", 0.2)
    task_id = TaskManager.create_task("test_op", "ds")

    async def _hung_step():
        await asyncio.sleep(30)
        return {"ok": True}

    # Act
    asyncio.run(TaskManager.run_background(task_id, _hung_step))

    # Assert — timed out → FAILED, not stranded in running.
    t = TaskManager._tasks[task_id]
    assert t.status == TaskStatus.FAILED
    assert "max lifetime" in (t.error or "")
