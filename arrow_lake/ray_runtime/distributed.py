"""Distributed processing via Ray foreach API (Story 6.8).

Provides parallel data processing across a Ray cluster:
- foreach: apply a function to partitions in parallel
- Auto-scaling support with configurable worker bounds
- Automatic task rescheduling on worker failure
"""

from __future__ import annotations

from collections.abc import Callable

import structlog
from pyarrow import Table

from arrow_lake.exceptions import ErrorCode, RayRuntimeError

logger = structlog.get_logger(__name__)

__all__ = [
    "AutoScaleConfig",
    "ProcessingResult",
    "_partition_table",
    "foreach",
]


class ProcessingResult:
    """Result of a distributed foreach operation.

    Attributes:
        output: Merged PyArrow Table from all workers, or None if no results.
        num_partitions: Number of partitions processed.
        num_failed: Number of partition failures.
        errors: List of error messages from failed partitions.
    """

    __slots__ = ("_errors", "_num_failed", "_num_partitions", "_output")

    def __init__(
        self,
        output: Table | None = None,
        num_partitions: int = 0,
        num_failed: int = 0,
        errors: list[str] | None = None,
    ) -> None:
        self._output = output
        self._num_partitions = num_partitions
        self._num_failed = num_failed
        self._errors = errors or []

    @property
    def output(self) -> Table | None:
        return self._output

    @property
    def num_partitions(self) -> int:
        return self._num_partitions

    @property
    def num_failed(self) -> int:
        return self._num_failed

    @property
    def errors(self) -> list[str]:
        return list(self._errors)

    @property
    def success(self) -> bool:
        return self._num_failed == 0

    def __repr__(self) -> str:
        return (
            f"ProcessingResult("
            f"partitions={self._num_partitions}, "
            f"failed={self._num_failed}, "
            f"success={self.success})"
        )


class AutoScaleConfig:
    """Configuration for Ray auto-scaling behavior.

    Attributes:
        min_workers: Minimum number of Ray workers.
        max_workers: Maximum number of Ray workers.
        use_gpu: Whether to include GPU workers.
    """

    __slots__ = ("_max_workers", "_min_workers", "_use_gpu")

    def __init__(
        self,
        min_workers: int = 2,
        max_workers: int = 10,
        use_gpu: bool = False,
    ) -> None:
        if min_workers < 1:
            raise ValueError(f"min_workers must be >= 1, got {min_workers}")
        if max_workers < min_workers:
            raise ValueError(f"max_workers ({max_workers}) must be >= min_workers ({min_workers})")
        self._min_workers = min_workers
        self._max_workers = max_workers
        self._use_gpu = use_gpu

    @property
    def min_workers(self) -> int:
        return self._min_workers

    @property
    def max_workers(self) -> int:
        return self._max_workers

    @property
    def use_gpu(self) -> bool:
        return self._use_gpu


def _partition_table(table: Table, num_partitions: int) -> list[Table]:
    """Split a PyArrow Table into roughly equal partitions."""
    if num_partitions <= 1 or table.num_rows == 0:
        return [table]

    actual_partitions = min(num_partitions, table.num_rows)
    row_counts = [table.num_rows // actual_partitions] * actual_partitions
    remainder = table.num_rows % actual_partitions
    for i in range(remainder):
        row_counts[i] += 1

    partitions: list[Table] = []
    offset = 0
    for count in row_counts:
        partitions.append(table.slice(offset, count))
        offset += count
    return partitions


def foreach(
    table: Table,
    fn: Callable[[Table], Table],
    *,
    num_partitions: int = 4,
    autoscale: AutoScaleConfig | None = None,
    max_retries: int = 1,
    batch_concurrency: int | None = None,
) -> ProcessingResult:
    """Apply a function to table partitions in parallel via Ray.

    Args:
        table: Input PyArrow Table to process.
        fn: Processing function (Table -> Table).
        num_partitions: Number of parallel partitions.
        autoscale: Auto-scaling configuration.
        max_retries: Max retries per partition on worker failure.
        batch_concurrency: Max concurrent tasks (default: num_partitions).

    Returns:
        ProcessingResult with merged output and error details.

    Raises:
        RayRuntimeError: If Ray is not available.
        TypeError: If fn is not callable.
    """
    if fn is None or not callable(fn):
        raise TypeError(f"fn must be callable, got {type(fn).__name__}")

    try:
        import ray
    except ImportError as exc:
        raise RayRuntimeError(
            error_code=ErrorCode.RAY_RUNTIME_PLACEMENT_FAILED,
            message="Ray is not installed; cannot use distributed foreach",
        ) from exc

    if not ray.is_initialized():
        raise RayRuntimeError(
            error_code=ErrorCode.RAY_RUNTIME_PLACEMENT_FAILED,
            message="Ray is not initialized; call initialize_ray() first",
        )

    # Apply autoscale bounds to partition count
    effective_partitions = num_partitions
    if autoscale is not None:
        effective_partitions = min(num_partitions, autoscale.max_workers)
        effective_partitions = max(effective_partitions, autoscale.min_workers)
        logger.info(
            "distributed_autoscale_config",
            min_workers=autoscale.min_workers,
            max_workers=autoscale.max_workers,
            use_gpu=autoscale.use_gpu,
            effective_partitions=effective_partitions,
        )

    concurrency = batch_concurrency or effective_partitions
    partitions = _partition_table(table, effective_partitions)

    if table.num_rows == 0:
        return ProcessingResult(output=table, num_partitions=0)

    logger.info(
        "distributed_foreach_start",
        partitions=len(partitions),
        concurrency=concurrency,
    )

    @ray.remote(max_retries=max_retries)
    def _process_partition(part: Table) -> Table:
        return fn(part)

    results: list[Table] = []
    errors: list[str] = []

    # Submit and collect in batches to respect concurrency limit
    for batch_start in range(0, len(partitions), concurrency):
        batch_partitions = partitions[batch_start : batch_start + concurrency]
        batch_refs = [_process_partition.remote(p) for p in batch_partitions]

        for j, ref in enumerate(batch_refs):
            partition_idx = batch_start + j
            try:
                result = ray.get(ref, timeout=300)
                results.append(result)
            except Exception as exc:
                errors.append(f"partition-{partition_idx}: {exc}")
                logger.error(
                    "distributed_partition_failed",
                    partition=partition_idx,
                    error=str(exc),
                )

    # Merge successful results
    output: Table | None = None
    if results:
        import pyarrow as pa

        output = pa.concat_tables(results)

    proc_result = ProcessingResult(
        output=output,
        num_partitions=len(partitions),
        num_failed=len(errors),
        errors=errors,
    )
    logger.info(
        "distributed_foreach_complete",
        total_partitions=len(partitions),
        successful=len(results),
        failed=len(errors),
    )
    return proc_result
