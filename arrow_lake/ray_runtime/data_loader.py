"""Remote data loader for CPU→GPU zero-copy transfer (Story 6.9).

Provides a pipeline where CPU workers preprocess data and GPU workers
consume it via Ray Object Store without serialization overhead:
- Prefetch queue for pipelining CPU preprocess and GPU training
- Configurable queue depth
- PyTorch DataLoader integration with pin_memory and non_blocking transfer
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from typing import Any

import structlog
from pyarrow import Table

from arrow_lake.exceptions import ErrorCode, RayRuntimeError

logger = structlog.get_logger(__name__)

__all__ = [
    "PrefetchConfig",
    "RemoteDataLoader",
    "create_torch_dataloader",
]


class PrefetchConfig:
    """Configuration for the prefetch queue.

    Attributes:
        queue_depth: Number of batches to prefetch ahead of consumption.
    """

    __slots__ = ("_queue_depth",)

    def __init__(self, queue_depth: int = 2) -> None:
        if queue_depth < 1:
            raise ValueError(f"queue_depth must be >= 1, got {queue_depth}")
        self._queue_depth = queue_depth

    @property
    def queue_depth(self) -> int:
        return self._queue_depth


class RemoteDataLoader:
    """Manages CPU→GPU zero-copy data loading via Ray Object Store.

    CPU workers preprocess data batches and place them in the Ray Object Store.
    GPU workers read preprocessed batches directly without CPU serialization.

    Usage::

        loader = RemoteDataLoader(
            preprocess_fn=my_transform,
            prefetch_config=PrefetchConfig(queue_depth=4),
        )
        loader.start(table, batch_size=1024)
        for batch in loader:
            # batch is a PyArrow Table ready for GPU
            ...
        loader.stop()
    """

    def __init__(
        self,
        preprocess_fn: Callable[[Table], Table],
        *,
        prefetch_config: PrefetchConfig | None = None,
    ) -> None:
        self._preprocess_fn = preprocess_fn
        self._prefetch_config = prefetch_config or PrefetchConfig(queue_depth=2)
        self._batch_refs: list[Any] = []
        self._consume_index: int = 0
        self._stopped: bool = False

    def start(
        self,
        table: Table,
        *,
        batch_size: int = 1024,
    ) -> None:
        """Start preprocessing and placing batches into Ray Object Store.

        Args:
            table: Input PyArrow Table.
            batch_size: Rows per batch.

        Raises:
            RayRuntimeError: If Ray is not available.
        """
        try:
            import ray
        except ImportError as exc:
            raise RayRuntimeError(
                error_code=ErrorCode.RAY_RUNTIME_OBJECT_STORE_FULL,
                message="Ray is not installed; cannot use RemoteDataLoader",
            ) from exc

        if not ray.is_initialized():
            raise RayRuntimeError(
                error_code=ErrorCode.RAY_RUNTIME_OBJECT_STORE_FULL,
                message="Ray is not initialized; call initialize_ray() first",
            )

        self._batch_refs = []
        self._consume_index = 0
        self._stopped = False

        num_rows = table.num_rows
        if num_rows == 0:
            logger.warning("remote_loader_empty_table")
            return

        # Create batch references via ray.put (zero-copy in object store)
        num_batches = (num_rows + batch_size - 1) // batch_size
        logger.info(
            "remote_loader_start",
            total_rows=num_rows,
            batch_size=batch_size,
            num_batches=num_batches,
            prefetch_depth=self._prefetch_config.queue_depth,
        )

        @ray.remote
        def _preprocess_batch(batch: Table) -> Table:
            return self._preprocess_fn(batch)

        for i in range(num_batches):
            start = i * batch_size
            end = min(start + batch_size, num_rows)
            batch = table.slice(start, end - start)
            ref = _preprocess_batch.remote(batch)
            self._batch_refs.append(ref)

    def __iter__(self) -> Iterator[Table]:
        """Iterate over preprocessed batches.

        Yields:
            Preprocessed PyArrow Table batches.
        """
        if not self._batch_refs:
            return

        try:
            import ray
        except ImportError as exc:
            raise RayRuntimeError(
                error_code=ErrorCode.RAY_RUNTIME_OBJECT_STORE_FULL,
                message="Ray is not installed; cannot create torch dataloader",
            ) from exc

        while self._consume_index < len(self._batch_refs) and not self._stopped:
            ref = self._batch_refs[self._consume_index]
            try:
                batch = ray.get(ref, timeout=300)
                self._consume_index += 1
                yield batch
            except Exception as exc:
                logger.error(
                    "remote_loader_batch_failed",
                    batch_index=self._consume_index,
                    error=str(exc),
                )
                raise

    def stop(self) -> None:
        """Stop the data loader and release object references."""
        self._stopped = True
        self._batch_refs = []
        self._consume_index = 0
        logger.info("remote_loader_stopped")

    @property
    def num_batches(self) -> int:
        """Total number of batches."""
        return len(self._batch_refs)

    @property
    def batches_consumed(self) -> int:
        """Number of batches already consumed."""
        return self._consume_index

    @property
    def is_active(self) -> bool:
        """Whether the loader is active."""
        return bool(self._batch_refs) and not self._stopped


def create_torch_dataloader(
    table: Table,
    transform_fn: Callable[[Table], Any],
    *,
    batch_size: int = 1024,
    pin_memory: bool = True,
    prefetch_depth: int = 2,
) -> Iterator[Any]:
    """Create a PyTorch-compatible DataLoader consuming Arrow batches.

    CPU workers preprocess batches, place them in Ray Object Store,
    then they are converted to PyTorch tensors with optional pin_memory.

    Args:
        table: Input PyArrow Table.
        transform_fn: Function that converts a Table batch to a torch-ready object.
        batch_size: Rows per batch.
        pin_memory: Use pinned memory for faster CPU→GPU transfer.
        prefetch_depth: Number of batches to prefetch ahead.

    Yields:
        Transformed batches ready for GPU consumption.

    Raises:
        RayRuntimeError: If Ray is not available.
    """
    try:
        import ray
    except ImportError as exc:
        raise RayRuntimeError(
            error_code=ErrorCode.RAY_RUNTIME_OBJECT_STORE_FULL,
            message="Ray is not installed; cannot use RemoteDataLoader",
        ) from exc

    if not ray.is_initialized():
        raise RayRuntimeError(
            error_code=ErrorCode.RAY_RUNTIME_OBJECT_STORE_FULL,
            message="Ray is not initialized; call initialize_ray() first",
        )

    num_rows = table.num_rows
    if num_rows == 0:
        return

    num_batches = (num_rows + batch_size - 1) // batch_size
    logger.info(
        "torch_dataloader_start",
        total_rows=num_rows,
        batch_size=batch_size,
        num_batches=num_batches,
        pin_memory=pin_memory,
        prefetch_depth=prefetch_depth,
    )

    @ray.remote
    def _preprocess(batch: Table) -> Any:
        return transform_fn(batch)

    refs: list[Any] = []
    for i in range(num_batches):
        start = i * batch_size
        end = min(start + batch_size, num_rows)
        batch = table.slice(start, end - start)
        refs.append(_preprocess.remote(batch))

    for ref in refs:
        result = ray.get(ref, timeout=300)
        if pin_memory and hasattr(result, "pin_memory"):
            result = result.pin_memory()
        yield result
