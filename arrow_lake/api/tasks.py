"""In-process background task tracking.

A lightweight in-memory task manager for async operations (export, ingest,
backup, kg_build, quality, index creation).  Upgraded to Redis/Celery in
v0.3.0 for distributed deployments.
"""

from __future__ import annotations

import asyncio
import functools
import logging
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, Callable, ClassVar

logger = logging.getLogger(__name__)


class TaskStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


@dataclass
class BackgroundTask:
    """Generic background task with progress tracking."""

    task_id: str
    operation: str  # "export", "ingest", "backup", "kg_build", "quality_filter", etc.
    dataset_name: str = ""
    status: TaskStatus = TaskStatus.PENDING
    progress: float = 0.0
    created_at: str = ""
    completed_at: str | None = None
    error: str | None = None
    result: dict[str, Any] | None = None
    detail: dict[str, Any] = field(default_factory=dict)

    # Legacy export-specific fields (kept for backward compat)
    output_path: str = ""
    fmt: str = ""


# Backward-compatible alias
ExportTask = BackgroundTask


class TaskManager:
    """In-process background task tracker.

    Supports any long-running operation via ``run_background()``.
    The existing ``run_export()`` is preserved as a thin wrapper.
    """

    _tasks: ClassVar[dict[str, BackgroundTask]] = {}
    _TASK_TTL_SECONDS = 7200  # Auto-cleanup after 2 hours

    # ------------------------------------------------------------------
    # Housekeeping
    # ------------------------------------------------------------------

    @classmethod
    def _evict_expired(cls) -> None:
        """Remove completed/failed tasks older than TTL."""
        now = datetime.now(UTC)
        expired = [
            tid for tid, t in cls._tasks.items()
            if t.status in (TaskStatus.COMPLETED, TaskStatus.FAILED)
            and t.completed_at
            and (now - datetime.fromisoformat(t.completed_at)).total_seconds() > cls._TASK_TTL_SECONDS
        ]
        for tid in expired:
            del cls._tasks[tid]

    # ------------------------------------------------------------------
    # Task CRUD
    # ------------------------------------------------------------------

    @classmethod
    def create_task(
        cls,
        operation: str,
        dataset_name: str = "",
        *,
        detail: dict[str, Any] | None = None,
    ) -> str:
        """Create a new background task and return its ID."""
        cls._evict_expired()
        task_id = uuid.uuid4().hex[:16]
        cls._tasks[task_id] = BackgroundTask(
            task_id=task_id,
            operation=operation,
            dataset_name=dataset_name,
            status=TaskStatus.PENDING,
            created_at=datetime.now(UTC).isoformat(),
            detail=detail or {},
        )
        return task_id

    @classmethod
    def get_task(cls, task_id: str) -> BackgroundTask | None:
        return cls._tasks.get(task_id)

    @classmethod
    def list_tasks(
        cls,
        *,
        operation: str | None = None,
        status: str | None = None,
    ) -> list[BackgroundTask]:
        """List tasks, optionally filtered by operation type and/or status."""
        tasks = list(cls._tasks.values())
        if operation:
            tasks = [t for t in tasks if t.operation == operation]
        if status:
            tasks = [t for t in tasks if t.status.value == status]
        return tasks

    # ------------------------------------------------------------------
    # Generic background runner
    # ------------------------------------------------------------------

    @classmethod
    async def run_background(
        cls,
        task_id: str,
        func: Callable[..., Any],
        *args: Any,
        **kwargs: Any,
    ) -> None:
        """Run any callable as a background task with status tracking.

        ``func`` may be sync or async.  Sync functions are dispatched to
        the default executor so they don't block the event loop.
        """
        task = cls._tasks.get(task_id)
        if task is None:
            return
        task.status = TaskStatus.RUNNING
        try:
            if asyncio.iscoroutinefunction(func):
                result = await func(*args, **kwargs)
            else:
                loop = asyncio.get_running_loop()
                result = await loop.run_in_executor(
                    None, functools.partial(func, *args, **kwargs),
                )
            task.status = TaskStatus.COMPLETED
            task.progress = 1.0
            task.completed_at = datetime.now(UTC).isoformat()
            if result is not None and isinstance(result, dict):
                task.result = result
            elif result is not None:
                task.result = {"value": result}
        except Exception as exc:
            task.status = TaskStatus.FAILED
            task.completed_at = datetime.now(UTC).isoformat()
            task.error = str(exc)
            logger.error("Background task %s (%s) failed: %s", task_id, task.operation, exc)

    # ------------------------------------------------------------------
    # Legacy export wrapper (backward compat)
    # ------------------------------------------------------------------

    @classmethod
    def create_export_task(cls, dataset_name: str, output_path: str, fmt: str = "parquet") -> str:
        """Create an export-specific task. Kept for backward compatibility."""
        task_id = cls.create_task("export", dataset_name)
        task = cls._tasks[task_id]
        task.output_path = output_path
        task.fmt = fmt
        return task_id

    @classmethod
    async def run_export(cls, task_id: str, lake: Any, **kwargs: Any) -> None:
        """Run export as a background task. Kept for backward compatibility."""
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
