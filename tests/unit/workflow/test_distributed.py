"""Tests for Story 6.8 — Distributed Processing via Ray foreach."""

from __future__ import annotations

import types
from unittest.mock import MagicMock, patch

import pyarrow as pa
import pytest
from arrow_lake.exceptions import RayRuntimeError
from arrow_lake.ray_runtime.distributed import (
    AutoScaleConfig,
    ProcessingResult,
    _partition_table,
    foreach,
)


class TestAutoScaleConfig:
    """Test AutoScaleConfig validation."""

    def test_defaults(self) -> None:
        config = AutoScaleConfig()
        assert config.min_workers == 2
        assert config.max_workers == 10
        assert config.use_gpu is False

    def test_custom(self) -> None:
        config = AutoScaleConfig(min_workers=1, max_workers=20, use_gpu=True)
        assert config.min_workers == 1
        assert config.max_workers == 20
        assert config.use_gpu is True

    def test_min_workers_zero_raises(self) -> None:
        with pytest.raises(ValueError, match="min_workers must be >= 1"):
            AutoScaleConfig(min_workers=0)

    def test_max_less_than_min_raises(self) -> None:
        with pytest.raises(ValueError, match="max_workers"):
            AutoScaleConfig(min_workers=5, max_workers=3)


class TestProcessingResult:
    """Test ProcessingResult."""

    def test_success(self) -> None:
        result = ProcessingResult(num_partitions=4, num_failed=0)
        assert result.success is True
        assert result.num_partitions == 4
        assert result.num_failed == 0
        assert result.errors == []
        assert result.output is None

    def test_failure(self) -> None:
        result = ProcessingResult(
            num_partitions=4,
            num_failed=2,
            errors=["p0: timeout", "p2: oom"],
        )
        assert result.success is False
        assert result.num_failed == 2
        assert len(result.errors) == 2

    def test_errors_is_copy(self) -> None:
        result = ProcessingResult(errors=["e1"])
        result.errors.append("e2")
        assert len(result._errors) == 1

    def test_repr(self) -> None:
        result = ProcessingResult(num_partitions=4)
        assert "partitions=4" in repr(result)
        assert "success=True" in repr(result)

    def test_with_output(self) -> None:
        table = pa.table({"a": [1, 2, 3]})
        result = ProcessingResult(output=table, num_partitions=1)
        assert result.output is not None
        assert result.output.num_rows == 3


class TestPartitionTable:
    """Test _partition_table utility."""

    def test_single_partition(self) -> None:
        table = pa.table({"a": range(10)})
        parts = _partition_table(table, num_partitions=1)
        assert len(parts) == 1
        assert parts[0].num_rows == 10

    def test_even_split(self) -> None:
        table = pa.table({"a": range(10)})
        parts = _partition_table(table, num_partitions=5)
        assert len(parts) == 5
        assert all(p.num_rows == 2 for p in parts)

    def test_uneven_split(self) -> None:
        table = pa.table({"a": range(10)})
        parts = _partition_table(table, num_partitions=3)
        assert len(parts) == 3
        total = sum(p.num_rows for p in parts)
        assert total == 10

    def test_empty_table(self) -> None:
        table = pa.table({"a": []})
        parts = _partition_table(table, num_partitions=4)
        assert len(parts) == 1
        assert parts[0].num_rows == 0

    def test_more_partitions_than_rows(self) -> None:
        table = pa.table({"a": range(3)})
        parts = _partition_table(table, num_partitions=10)
        assert len(parts) == 3


def _make_ray_mock(*, initialized: bool = True) -> types.ModuleType:
    """Create a fake ray module that simulates remote execution locally.

    The key pattern: `@ray.remote(max_retries=N)` is a decorator factory.
    It returns a wrapper that has a `.remote()` method for async execution.
    """
    fake_ray = types.ModuleType("ray")
    fake_ray.is_initialized = MagicMock(return_value=initialized)

    _refs: list[object] = []

    class RemoteWrapper:
        """Mimics a Ray remote function handle."""

        def __init__(self, fn, **_kwargs):
            self._fn = fn

        def remote(self, *args, **kwargs):
            result = self._fn(*args, **kwargs)
            _refs.append(result)
            return len(_refs) - 1  # return ref index

    def _remote(fn=None, **kwargs):
        if fn is None:
            # Called as @ray.remote(max_retries=N) — return decorator
            def decorator(f):
                return RemoteWrapper(f, **kwargs)

            return decorator
        else:
            # Called as @ray.remote (no kwargs)
            return RemoteWrapper(fn, **kwargs)

    def _get(ref, timeout=None):
        if isinstance(ref, int) and 0 <= ref < len(_refs):
            return _refs[ref]
        return ref

    fake_ray.remote = MagicMock(side_effect=_remote)
    fake_ray.get = MagicMock(side_effect=_get)
    return fake_ray


class TestForeach:
    """Test foreach distributed processing."""

    def test_raises_when_ray_not_installed(self) -> None:
        table = pa.table({"a": range(10)})
        with (
            patch.dict("sys.modules", {"ray": None}),
            pytest.raises(RayRuntimeError, match="Ray is not installed"),
        ):
            foreach(table, lambda t: t)

    def test_raises_when_ray_not_initialized(self) -> None:
        fake_ray = _make_ray_mock(initialized=False)
        table = pa.table({"a": range(10)})
        with (
            patch.dict("sys.modules", {"ray": fake_ray}),
            pytest.raises(RayRuntimeError, match="not initialized"),
        ):
            foreach(table, lambda t: t)

    def test_foreach_empty_table(self) -> None:
        """Empty table returns immediately without Ray remote calls."""
        fake_ray = _make_ray_mock(initialized=True)
        table = pa.table({"a": []})
        with patch.dict("sys.modules", {"ray": fake_ray}):
            result = foreach(table, lambda t: t, num_partitions=4)
        assert result.success is True
        assert result.num_partitions == 0
        assert result.output is not None
        assert result.output.num_rows == 0

    def test_foreach_with_ray(self) -> None:
        """Processes partitions and merges results."""
        fake_ray = _make_ray_mock(initialized=True)
        table = pa.table({"a": range(20), "b": range(20, 40)})

        with patch.dict("sys.modules", {"ray": fake_ray}):
            result = foreach(table, lambda t: t, num_partitions=4)

        assert result.success is True
        assert result.output is not None
        assert result.output.num_rows == 20
        assert result.num_partitions == 4

    def test_foreach_with_transform(self) -> None:
        """Applies a transform function to each partition."""
        fake_ray = _make_ray_mock(initialized=True)
        table = pa.table({"a": range(10)})

        import pyarrow.compute as pc

        def double_col(t: pa.Table) -> pa.Table:
            return t.set_column(0, "a", pc.multiply(t.column("a"), 2))

        with patch.dict("sys.modules", {"ray": fake_ray}):
            result = foreach(table, double_col, num_partitions=2)

        assert result.success is True
        assert result.output is not None
        values = result.output.column("a").to_pylist()
        assert values == [0, 2, 4, 6, 8, 10, 12, 14, 16, 18]

    def test_foreach_with_autoscale(self) -> None:
        """Autoscale config clamps effective partitions to max_workers."""
        fake_ray = _make_ray_mock(initialized=True)
        table = pa.table({"a": range(6)})
        config = AutoScaleConfig(min_workers=2, max_workers=2, use_gpu=True)

        with patch.dict("sys.modules", {"ray": fake_ray}):
            result = foreach(table, lambda t: t, num_partitions=6, autoscale=config)

        assert result.success is True
        # max_workers=2 should clamp to 2 effective partitions
        assert result.num_partitions == 2

    def test_foreach_autoscale_respects_min_workers(self) -> None:
        """Autoscale min_workers raises effective partitions when too low."""
        fake_ray = _make_ray_mock(initialized=True)
        table = pa.table({"a": range(10)})
        config = AutoScaleConfig(min_workers=4, max_workers=10, use_gpu=False)

        with patch.dict("sys.modules", {"ray": fake_ray}):
            result = foreach(table, lambda t: t, num_partitions=2, autoscale=config)

        assert result.success is True
        # min_workers=4 should raise effective partitions to 4
        assert result.num_partitions == 4

    def test_foreach_single_partition(self) -> None:
        """Single partition still works correctly."""
        fake_ray = _make_ray_mock(initialized=True)
        table = pa.table({"x": [1, 2, 3]})

        with patch.dict("sys.modules", {"ray": fake_ray}):
            result = foreach(table, lambda t: t, num_partitions=1)

        assert result.success is True
        assert result.num_partitions == 1
        assert result.output.num_rows == 3

    def test_foreach_batch_concurrency(self) -> None:
        """batch_concurrency limits how many tasks are submitted at once."""
        fake_ray = _make_ray_mock(initialized=True)
        table = pa.table({"a": range(8)})

        with patch.dict("sys.modules", {"ray": fake_ray}):
            result = foreach(
                table,
                lambda t: t,
                num_partitions=8,
                batch_concurrency=2,
            )

        assert result.success is True
        assert result.output.num_rows == 8
        assert result.num_partitions == 8
