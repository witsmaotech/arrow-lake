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
            dataset_name="ds",
            output_path="/tmp/out",
            fmt="parquet",
            status=TaskStatus.COMPLETED,
            completed_at=old_time,
        )
        TaskManager._tasks["fresh"] = ExportTask(
            task_id="fresh",
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
