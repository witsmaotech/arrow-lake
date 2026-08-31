"""In-process background task tracking with optional Redis state sharing.

A lightweight task manager for async operations (export, ingest,
backup, kg_build, quality, index creation).

When ``init_redis_store()`` is called at app startup with a valid RedisConfig,
all task state is mirrored to Redis so any uvicorn worker can query progress.
Falls back to pure in-memory when Redis is unavailable.
"""

from __future__ import annotations

import asyncio
import contextlib
import functools
import logging
import os
import uuid
from collections.abc import Callable, Coroutine
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, ClassVar

from arrow_lake.api._redis_task_store import RedisTaskStore
from arrow_lake.api.utils import ingest_executor

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
    user_id: int | None = None  # v1.9.3: task owner → my-workspace notification

    # Legacy export-specific fields (kept for backward compat)
    output_path: str = ""
    fmt: str = ""

    def to_dict(self) -> dict[str, Any]:
        """Serialize to a flat dict suitable for Redis HASH storage."""
        d: dict[str, Any] = {
            "task_id": self.task_id,
            "operation": self.operation,
            "dataset_name": self.dataset_name,
            "status": self.status.value,
            "progress": str(self.progress),
            "created_at": self.created_at,
            "completed_at": self.completed_at or "",
            "error": self.error or "",
        }
        if self.result is not None:
            d["result"] = self.result
        if self.detail:
            d["detail"] = self.detail
        if self.output_path:
            d["output_path"] = self.output_path
        if self.fmt:
            d["fmt"] = self.fmt
        return d

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> BackgroundTask:
        """Deserialize from a flat dict (Redis HASH or API response)."""
        # Handle JSON-encoded strings for dict/list fields
        detail = data.get("detail")
        if isinstance(detail, str):
            import json as _json
            try:
                detail = _json.loads(detail)
            except (ValueError, TypeError):
                detail = {}
        result = data.get("result")
        if isinstance(result, str):
            import json as _json
            try:
                result = _json.loads(result)
            except (ValueError, TypeError):
                result = None

        return cls(
            task_id=data.get("task_id", ""),
            operation=data.get("operation", ""),
            dataset_name=data.get("dataset_name", ""),
            status=TaskStatus(data.get("status", "pending")),
            progress=float(data.get("progress", 0)),
            created_at=data.get("created_at", ""),
            completed_at=data.get("completed_at") or None,
            error=data.get("error") or None,
            result=result,
            detail=detail or {},
            output_path=data.get("output_path", ""),
            fmt=data.get("fmt", ""),
        )


# Backward-compatible alias
ExportTask = BackgroundTask


# ---------------------------------------------------------------------------
# Fire-and-forget strong-reference registry
# ---------------------------------------------------------------------------
# asyncio.create_task without a held reference can be garbage-collected
# mid-flight, freezing the task at RUNNING (run_background's finally never
# runs). Holding each task here until completion prevents that. Consolidates
# the correct pattern already used by _lake_kg._kg_bg_tasks and quality._BG_TASKS.
_BG_TASKS: set[asyncio.Task[Any]] = set()


def spawn_background(coro: Coroutine[Any, Any, Any]) -> asyncio.Task[Any]:
    """Schedule a coroutine as a tracked background task with a strong ref.

    Use for every fire-and-forget ``TaskManager.run_background`` dispatch so the
    GC cannot reclaim the task while pending. Auto-removed on completion.
    """
    task = asyncio.create_task(coro)
    _BG_TASKS.add(task)
    task.add_done_callback(_BG_TASKS.discard)
    return task


# run_background writes a heartbeat every this many seconds while a task runs,
# so a live worker can be distinguished from one that died mid-task.
_HEARTBEAT_INTERVAL_SECONDS = 20.0
# A running/pending task whose heartbeat (or created_at, for legacy tasks that
# predate heartbeats) is older than this is considered orphaned — its owning
# worker exited (dev --reload, gunicorn recycle/timeout, OOM, crash) before
# run_background's finally could sync the terminal status.
_ORPHAN_STALE_SECONDS = 180.0
# Hard ceiling on a single background task's run time — a BACKSTOP only. The
# principled "is this hung?" signal is per-operation timeouts (embed already
# enforces 30s/call) + the heartbeat (worker-death detection). This ceiling
# catches the residual case: a blocking call that slipped through WITHOUT a
# per-call timeout (a storage write, docling parse, network socket stall) and
# so hangs forever with the worker still alive (heartbeat fresh → reaper
# spares it). It must stay GENEROUS so it never kills a legit long ingest
# (docling on CPU can run several hours for big PDFs). Tunable via env so heavy
# workloads can raise it without a code change; set 0 to disable.
_TASK_MAX_LIFETIME_SECONDS = 14400.0


def _resolve_max_lifetime() -> float | None:
    """Resolve the task lifetime ceiling (seconds) or None to disable.

    Reads ``ARROW_LAKE_TASK_MAX_LIFETIME_SECONDS`` env (override); falls back
    to the module default. ``<= 0`` disables the ceiling (no wait_for).
    """
    raw = os.environ.get("ARROW_LAKE_TASK_MAX_LIFETIME_SECONDS")
    if raw:
        try:
            val = float(raw)
        except (TypeError, ValueError):
            return _TASK_MAX_LIFETIME_SECONDS
        return val if val > 0 else None
    return _TASK_MAX_LIFETIME_SECONDS


# M-5(v1.10.7 review):同步后台任务的执行槽闸——与 ingest_executor 池同
# 尺寸;获槽后才转 RUNNING/启心跳/计 lifetime。lazy 创建(asyncio 原语
# 绑 loop;每进程单 loop,gunicorn 多 worker 各自独立,语义正确)。
_ingest_slots: asyncio.Semaphore | None = None


def _ingest_slots_gate() -> asyncio.Semaphore:
    global _ingest_slots
    if _ingest_slots is None:
        workers = getattr(ingest_executor, "_max_workers", None) or 8
        _ingest_slots = asyncio.Semaphore(workers)
    return _ingest_slots


class TaskManager:
    """In-process background task tracker with optional Redis sharing.

    Supports any long-running operation via ``run_background()``.
    The existing ``run_export()`` is preserved as a thin wrapper.
    """

    _tasks: ClassVar[dict[str, BackgroundTask]] = {}
    _redis_store: ClassVar[RedisTaskStore | None] = None
    # v1.9.0: durable history store (TaskHistoryStore | None). When set,
    # completed/failed tasks are recorded beyond the Redis TTL.
    _history_store: ClassVar[Any] = None
    _user_state_store: ClassVar[Any] = None  # v1.9.3: my-workspace notifications
    _TASK_TTL_SECONDS = 7200  # Auto-cleanup after 2 hours

    # ------------------------------------------------------------------
    # Initialization
    # ------------------------------------------------------------------

    @classmethod
    def init_redis_store(cls, redis_config: Any) -> None:
        """Initialize the Redis-backed task store.

        Call once at app startup.  If Redis is unavailable, tasks stay
        in-process only (graceful degradation).
        """
        from arrow_lake.api._redis_task_store import create_task_store

        store = create_task_store(redis_config)
        if store is not None:
            cls._redis_store = store
            logger.info("TaskManager: Redis store enabled")
        else:
            logger.info("TaskManager: using in-memory task store")

    @classmethod
    def shutdown_redis_store(cls) -> None:
        """Shutdown the Redis store (call at app shutdown)."""
        if cls._redis_store is not None:
            cls._redis_store.shutdown()
            cls._redis_store = None

    @classmethod
    def init_history_store(cls, store: Any) -> None:
        """v1.9.0: enable durable task-history persistence (libSQL)."""
        cls._history_store = store
        if store is not None:
            logger.info("TaskManager: history store enabled")

    @classmethod
    def init_user_state_store(cls, store: Any) -> None:
        """v1.9.3: enable my-workspace notifications on task completion."""
        cls._user_state_store = store

    @classmethod
    def _persist_history(cls, task: BackgroundTask) -> None:
        """Record terminal tasks into history + notify the owner (my-workspace feed)."""
        if task.status not in (TaskStatus.COMPLETED, TaskStatus.FAILED):
            return
        if cls._history_store is not None:
            try:
                cls._history_store.record(task.to_dict())
            except Exception as exc:
                logger.warning("TaskManager: history persist failed for %s: %s", task.task_id, exc)
        cls._notify_owner(task)

    @classmethod
    def _notify_owner(cls, task: BackgroundTask) -> None:
        """v1.9.3: push a my-workspace notification to the task owner (best-effort)."""
        if cls._user_state_store is None or task.user_id is None:
            return
        ds = f" · {task.dataset_name}" if task.dataset_name else ""
        if task.status == TaskStatus.COMPLETED:
            msg, kind = f"{task.operation} 完成{ds}", "success"
        else:
            err = (task.error or "").strip()[:160]
            msg, kind = f"{task.operation} 失败{ds}{': ' + err if err else ''}", "error"
        try:
            cls._user_state_store.notify(task.user_id, msg, kind=kind)
        except Exception as exc:
            logger.warning("TaskManager: notify owner failed for %s: %s", task.task_id, exc)

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
        # Also evict from Redis
        if cls._redis_store is not None:
            cls._redis_store.evict_expired()

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
        user_id: int | None = None,
    ) -> str:
        """Create a new background task and return its ID."""
        cls._evict_expired()
        task_id = uuid.uuid4().hex[:16]
        task = BackgroundTask(
            task_id=task_id,
            operation=operation,
            dataset_name=dataset_name,
            status=TaskStatus.PENDING,
            created_at=datetime.now(UTC).isoformat(),
            detail=detail or {},
            user_id=user_id,
        )
        cls._tasks[task_id] = task

        # Mirror to Redis
        if cls._redis_store is not None:
            cls._redis_store.create_task(task.to_dict())

        return task_id

    @classmethod
    def get_task(cls, task_id: str) -> BackgroundTask | None:
        """Get a task by ID, checking Redis first for multi-worker visibility."""
        # 1. Check local in-memory first (most up-to-date for running tasks)
        local = cls._tasks.get(task_id)
        if local is not None and local.status in (TaskStatus.RUNNING, TaskStatus.PENDING):
            return local

        # 2. Check Redis (may have been created by another worker)
        if cls._redis_store is not None:
            remote = cls._redis_store.get_task(task_id)
            if remote is not None:
                bt = BackgroundTask.from_dict(remote)
                # Cache locally
                cls._tasks[task_id] = bt
                return bt

        # 3. v1.9.0: durable history (completed/failed tasks beyond Redis 2h TTL)
        if cls._history_store is not None and local is None:
            try:
                hist = cls._history_store.get(task_id)
            except Exception:
                hist = None
            if hist is not None:
                return BackgroundTask.from_dict(hist)

        # 4. Fall back to local (completed/failed)
        return local

    @classmethod
    def list_tasks(
        cls,
        *,
        operation: str | None = None,
        status: str | None = None,
    ) -> list[BackgroundTask]:
        """List tasks, optionally filtered by operation type and/or status.

        Merges in-memory, Redis, and durable history results (dedup by task_id).
        History covers completed/failed tasks that survive restarts and the
        Redis 2h TTL — without it ``/tasks`` looks empty on a fresh boot.
        """
        seen: dict[str, BackgroundTask] = {}

        # Local tasks first (most authoritative for running)
        for t in cls._tasks.values():
            seen[t.task_id] = t

        # Merge from Redis (fills gaps for tasks from other workers)
        if cls._redis_store is not None:
            for tid in cls._redis_store.list_task_ids():
                if tid not in seen:
                    remote = cls._redis_store.get_task(tid)
                    if remote is not None:
                        seen[tid] = BackgroundTask.from_dict(remote)

        # Merge durable history (completed/failed across restarts & Redis 2h TTL)
        if cls._history_store is not None:
            try:
                for h in cls._history_store.list(
                    operation=operation, status=status, limit=200
                ):
                    tid = h.get("task_id")
                    if tid and tid not in seen:
                        seen[tid] = BackgroundTask.from_dict(h)
            except Exception:
                logger.exception("task_history list failed")

        tasks = list(seen.values())
        if operation:
            tasks = [t for t in tasks if t.operation == operation]
        if status:
            tasks = [t for t in tasks if t.status.value == status]
        return tasks

    @classmethod
    def _sync_to_redis(cls, task: BackgroundTask) -> None:
        """Push current task state to Redis."""
        if cls._redis_store is not None:
            cls._redis_store.update_task(task.task_id, task.to_dict())




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
        the dedicated ingest executor so they don't block the event loop.

        A heartbeat is written while running so :meth:`reap_orphaned_tasks` can
        tell a live task from one whose owning worker died (reload/recycle/
        crash) — without it, such tasks strand in ``running`` forever because
        this method's ``finally`` never runs on a dead process.

        M-5(v1.10.7 review,发版前清偿):同步任务**先占槽再入池**——池满
        排队期间状态=PENDING+``detail.queued_at``(**不**假显 RUNNING、
        心跳不跳),``max-lifetime`` 只计实际执行(获槽后起算)。旧语义
        把排队时间计入 lifetime:从未启动的任务被标 failed 而 executor
        线程稍后仍会执行(restore 重跑=双跑风险)。排队占用回收豁免:
        reaper 只在进程启动期跑,活进程的排队任务不会被误收。
        """
        task = cls._tasks.get(task_id)
        if task is None:
            return

        is_async_fn = asyncio.iscoroutinefunction(func)

        async def _mark_running() -> None:
            task.status = TaskStatus.RUNNING
            task.detail.pop("queued_at", None)
            cls._sync_to_redis(task)

        if is_async_fn:
            await _mark_running()
        else:
            # 排队可见(诚实态):未获槽前不装 RUNNING
            task.status = TaskStatus.PENDING
            task.detail["queued_at"] = datetime.now(UTC).isoformat()
            cls._sync_to_redis(task)

        async def _heartbeat() -> None:
            while True:
                await asyncio.sleep(_HEARTBEAT_INTERVAL_SECONDS)
                if task.status != TaskStatus.RUNNING:
                    continue  # M-5:排队期不假跳心跳
                task.detail["heartbeat"] = datetime.now(UTC).isoformat()
                cls._sync_to_redis(task)

        heartbeat = asyncio.create_task(_heartbeat())
        try:
            if is_async_fn:
                coro = func(*args, **kwargs)
            else:
                # v1.10.7 WP2 (review H8): the shared default executor is what
                # run_sync uses — heavy ingest there starved every route. The
                # dedicated ingest pool keeps saturation impact local to ingest.
                #
                # M-5: async gate sized to the executor pool — acquire BEFORE
                # submitting so queue time is neither heartbeated nor
                # lifetime-counted (double-run class eliminated at the source).
                gate = _ingest_slots_gate()

                async def _gated() -> Any:
                    await gate.acquire()
                    try:
                        await _mark_running()
                        loop = asyncio.get_running_loop()
                        return await loop.run_in_executor(
                            ingest_executor,
                            functools.partial(func, *args, **kwargs),
                        )
                    finally:
                        gate.release()

                coro = _gated()
            # Bound the run time as a backstop: a hung sync step (no client-side
            # timeout) must still transition to FAILED instead of idling in
            # running forever. The executor thread can't be force-killed, but
            # the status becomes correct and the de-dup guard unblocks.
            # (M-5: gate acquisition happens *outside* the lifetime window.)
            lifetime = _resolve_max_lifetime()
            try:
                if lifetime is None:
                    result = await coro
                else:
                    result = await asyncio.wait_for(coro, timeout=lifetime)
            except TimeoutError as exc:
                raise TimeoutError(
                    f"task exceeded {lifetime:.0f}s max lifetime"
                ) from exc
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
        finally:
            heartbeat.cancel()
            with contextlib.suppress(BaseException):
                await heartbeat
            task.detail.pop("heartbeat", None)
            cls._sync_to_redis(task)
            cls._persist_history(task)

    @classmethod
    def reap_orphaned_tasks(cls, *, stale_seconds: float = _ORPHAN_STALE_SECONDS) -> int:
        """Mark ``running``/``pending`` tasks whose owning worker has exited as failed.

        A background task only progresses while its worker is alive. If the
        worker dies (dev ``--reload``, gunicorn worker recycle/timeout, OOM,
        crash), :meth:`run_background`'s ``finally`` never runs and the task is
        stranded in ``running``/``pending`` forever — the task queue shows it
        endlessly "running" and the console's ingest de-dup guard blocks the
        next incremental ingest (2026-08-07 outage).

        Called at every worker startup: a fresh process cannot still be
        executing a task from a previous lifetime. A task is reclaimed when its
        heartbeat (or ``created_at`` for legacy tasks predating heartbeats) is
        older than ``stale_seconds`` — the age guard spares a task a sibling
        worker just started.
        """
        now = datetime.now(UTC)
        ids: set[str] = set()
        if cls._redis_store is not None:
            ids.update(cls._redis_store.list_task_ids())
        ids.update(cls._tasks.keys())

        reaped = 0
        for tid in ids:
            task = cls.get_task(tid)
            if task is None or task.status not in (TaskStatus.RUNNING, TaskStatus.PENDING):
                continue
            stamp = task.detail.get("heartbeat") if task.detail else None
            try:
                ref = datetime.fromisoformat(stamp) if stamp else datetime.fromisoformat(task.created_at)
            except (TypeError, ValueError):
                ref = now
            age = (now - ref).total_seconds()
            if age < stale_seconds:
                continue
            task.status = TaskStatus.FAILED
            task.completed_at = now.isoformat()
            task.error = "orphaned: owning worker exited before completion"
            task.detail.pop("heartbeat", None)
            cls._tasks[tid] = task
            cls._sync_to_redis(task)
            cls._persist_history(task)
            reaped += 1
            logger.warning(
                "TaskManager: reaped orphaned %s task %s (age=%.0fs)",
                task.operation, tid, age,
            )
        return reaped

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
        cls._sync_to_redis(task)
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
        finally:
            cls._sync_to_redis(task)
            cls._persist_history(task)
