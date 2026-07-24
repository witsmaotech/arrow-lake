"""Tests for arrow_lake.api.tasks — TaskManager and ExportTask."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock

import pytest

from arrow_lake.api.tasks import ExportTask, TaskManager, TaskStatus


class TestTaskManagerEviction:
    """Cover L54: del cls._tasks[tid] in _evict_expired."""

    def setup_method(self) -> None:
        TaskManager._tasks.clear()

    def teardown_method(self) -> None:
        TaskManager._tasks.clear()

    def test_evict_removes_expired_completed_task(self) -> None:
        old_time = (datetime.now(UTC) - timedelta(hours=2)).isoformat()
        TaskManager._tasks["old"] = ExportTask(
            task_id="old",
            operation="export",
            dataset_name="ds",
            output_path="/tmp/out",
            fmt="parquet",
            status=TaskStatus.COMPLETED,
            completed_at=old_time,
        )
        TaskManager._tasks["fresh"] = ExportTask(
            task_id="fresh",
            operation="export",
            dataset_name="ds",
            output_path="/tmp/out",
            fmt="parquet",
            status=TaskStatus.COMPLETED,
            completed_at=datetime.now(UTC).isoformat(),
        )
        # create_task triggers _evict_expired
        TaskManager.create_task("ds", "/tmp/new")
        assert "old" not in TaskManager._tasks
        assert "fresh" in TaskManager._tasks

    def test_evict_removes_expired_failed_task(self) -> None:
        old_time = (datetime.now(UTC) - timedelta(hours=2)).isoformat()
        TaskManager._tasks["failed_old"] = ExportTask(
            task_id="failed_old",
            operation="export",
            dataset_name="ds",
            output_path="/tmp/out",
            fmt="parquet",
            status=TaskStatus.FAILED,
            completed_at=old_time,
        )
        TaskManager.create_task("ds", "/tmp/new")
        assert "failed_old" not in TaskManager._tasks


class TestTaskManagerRunExport:
    """Cover L78: return when task is None in run_export."""

    def setup_method(self) -> None:
        TaskManager._tasks.clear()

    def teardown_method(self) -> None:
        TaskManager._tasks.clear()

    @pytest.mark.asyncio
    async def test_run_export_returns_for_missing_task(self) -> None:
        lake = MagicMock()
        lake.export = MagicMock(return_value=MagicMock(
            dataset_name="ds", output_path="/tmp", format="parquet",
            row_count=0, column_count=0, file_size_bytes=0, version="1",
        ))
        # Calling with a non-existent task_id should return without error
        await TaskManager.run_export("nonexistent_id", lake)
        lake.export.assert_not_called()


def _hist(
    task_id: str = "h1",
    status: str = "completed",
    operation: str = "export",
    dataset_name: str = "jd_ddd",
) -> dict:
    """Build a TaskHistoryStore.list() row (matches _row_to_task shape)."""
    return {
        "task_id": task_id,
        "operation": operation,
        "dataset_name": dataset_name,
        "status": status,
        "progress": 100.0,
        "result": None,
        "detail": None,
        "error": None,
        "created_at": "2026-01-01T00:00:00",
        "completed_at": "2026-01-01T00:01:00",
        "duration_ms": 60000,
    }


class TestTaskManagerListHistory:
    """list_tasks must merge durable history (regression: list omitted the
    history store, so /tasks looked empty after a restart / past Redis TTL)."""

    def setup_method(self) -> None:
        TaskManager._tasks.clear()
        self._orig_history = TaskManager._history_store
        self._orig_redis = TaskManager._redis_store

    def teardown_method(self) -> None:
        TaskManager._tasks.clear()
        TaskManager._history_store = self._orig_history
        TaskManager._redis_store = self._orig_redis

    def test_list_includes_history_when_memory_empty(self) -> None:
        TaskManager._redis_store = None
        store = MagicMock()
        store.list.return_value = [_hist("h1")]
        TaskManager._history_store = store

        tasks = TaskManager.list_tasks()

        ids = [t.task_id for t in tasks]
        assert "h1" in ids
        assert next(t for t in tasks if t.task_id == "h1").status == TaskStatus.COMPLETED

    def test_memory_is_authoritative_over_stale_history(self) -> None:
        # Same task_id running in memory but completed in (stale) history.
        TaskManager._tasks["dup"] = ExportTask(
            task_id="dup",
            operation="export",
            dataset_name="ds",
            output_path="/tmp",
            fmt="parquet",
            status=TaskStatus.RUNNING,
        )
        TaskManager._redis_store = None
        store = MagicMock()
        store.list.return_value = [_hist("dup", status="completed")]
        TaskManager._history_store = store

        tasks = TaskManager.list_tasks()
        dup = next(t for t in tasks if t.task_id == "dup")
        assert dup.status == TaskStatus.RUNNING

    def test_history_store_failure_is_soft(self) -> None:
        TaskManager._redis_store = None
        store = MagicMock()
        store.list.side_effect = RuntimeError("db down")
        TaskManager._history_store = store

        tasks = TaskManager.list_tasks()  # must not raise
        assert tasks == []

    def test_filters_forwarded_to_history_store(self) -> None:
        TaskManager._redis_store = None
        store = MagicMock()
        store.list.return_value = []
        TaskManager._history_store = store

        TaskManager.list_tasks(operation="export", status="completed")

        store.list.assert_called_once()
        assert store.list.call_args.kwargs == {
            "operation": "export",
            "status": "completed",
            "limit": 200,
        }
