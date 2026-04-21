"""In-process export task tracking.

A lightweight in-memory task manager for async export operations.
Upgraded to Redis/Celery in v0.3.0 for distributed deployments.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, ClassVar


class TaskStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


@dataclass
class ExportTask:
    task_id: str
    dataset_name: str
    output_path: str
    fmt: str
    status: TaskStatus = TaskStatus.PENDING
    progress: float = 0.0
    created_at: str = ""
    completed_at: str | None = None
    error: str | None = None
    result: dict[str, Any] | None = None


class TaskManager:
    """In-process export task tracker."""

    _tasks: ClassVar[dict[str, ExportTask]] = {}

    @classmethod
    def create_task(
        cls,
        dataset_name: str,
        output_path: str,
        fmt: str = "parquet",
    ) -> str:
        task_id = uuid.uuid4().hex[:16]
        cls._tasks[task_id] = ExportTask(
            task_id=task_id,
            dataset_name=dataset_name,
            output_path=output_path,
            fmt=fmt,
            status=TaskStatus.PENDING,
            created_at=datetime.now(UTC).isoformat(),
        )
        return task_id

    @classmethod
    def get_task(cls, task_id: str) -> ExportTask | None:
        return cls._tasks.get(task_id)

    @classmethod
    async def run_export(cls, task_id: str, lake: Any, **kwargs: Any) -> None:
        task = cls._tasks.get(task_id)
        if task is None:
            return
        task.status = TaskStatus.RUNNING
        try:
            result = lake.export(task.dataset_name, task.output_path, **kwargs)
            task.status = TaskStatus.COMPLETED
            task.progress = 1.0
            task.completed_at = datetime.now(UTC).isoformat()
            task.result = {
                "dataset_name": result.dataset_name,
                "output_path": result.output_path,
                "format": result.format,
                "row_count": result.row_count,
                "column_count": result.column_count,
                "file_size_bytes": result.file_size_bytes,
                "version": result.version,
            }
        except Exception as exc:
            task.status = TaskStatus.FAILED
            task.completed_at = datetime.now(UTC).isoformat()
            task.error = str(exc)
