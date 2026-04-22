"""Tests for Story 6.9 — Remote Data Loader (CPU→GPU zero-copy)."""

from __future__ import annotations

import types
from unittest.mock import MagicMock, patch

import pyarrow as pa
import pytest
from arrow_lake.exceptions import RayRuntimeError
from arrow_lake.ray_runtime.data_loader import (
    PrefetchConfig,
    RemoteDataLoader,
    create_torch_dataloader,
)


class _RemoteWrapper:
    """Mimics a Ray remote function handle with .remote() method."""

    def __init__(self, fn, **_kwargs):
        self._fn = fn

    def remote(self, *args, **kwargs):
        return self._fn(*args, **kwargs)


def _make_ray_mock(*, initialized: bool = True) -> types.ModuleType:
    """Create a fake ray module that simulates remote execution locally."""
    fake_ray = types.ModuleType("ray")
    fake_ray.is_initialized = MagicMock(return_value=initialized)

    def _remote(fn=None, **kwargs):
        if fn is None:

            def decorator(f):
                return _RemoteWrapper(f, **kwargs)

            return decorator
        else:
            return _RemoteWrapper(fn, **kwargs)

    fake_ray.remote = MagicMock(side_effect=_remote)
    fake_ray.get = MagicMock(side_effect=lambda ref, timeout=None: ref)
    return fake_ray


class TestPrefetchConfig:
    """Test PrefetchConfig validation."""

    def test_defaults(self) -> None:
        config = PrefetchConfig()
        assert config.queue_depth == 2

    def test_custom(self) -> None:
        config = PrefetchConfig(queue_depth=4)
        assert config.queue_depth == 4

    def test_zero_raises(self) -> None:
        with pytest.raises(ValueError, match="queue_depth must be >= 1"):
            PrefetchConfig(queue_depth=0)

    def test_negative_raises(self) -> None:
        with pytest.raises(ValueError, match="queue_depth must be >= 1"):
            PrefetchConfig(queue_depth=-1)


class TestRemoteDataLoader:
    """Test RemoteDataLoader."""

    def test_raises_when_ray_not_installed(self) -> None:
        loader = RemoteDataLoader(lambda t: t)
        table = pa.table({"a": range(10)})
        with (
            patch.dict("sys.modules", {"ray": None}),
            pytest.raises(RayRuntimeError, match="Ray is not installed"),
        ):
            loader.start(table)

    def test_raises_when_ray_not_initialized(self) -> None:
        fake_ray = _make_ray_mock(initialized=False)
        loader = RemoteDataLoader(lambda t: t)
        table = pa.table({"a": range(10)})
        with (
            patch.dict("sys.modules", {"ray": fake_ray}),
            pytest.raises(RayRuntimeError, match="not initialized"),
        ):
            loader.start(table)

    def test_empty_table_no_batches(self) -> None:
        fake_ray = _make_ray_mock(initialized=True)
        loader = RemoteDataLoader(lambda t: t)
        table = pa.table({"a": []})
        with patch.dict("sys.modules", {"ray": fake_ray}):
            loader.start(table, batch_size=10)
        assert loader.num_batches == 0
        assert not loader.is_active

    def test_stop_clears_state(self) -> None:
        fake_ray = _make_ray_mock(initialized=True)
        loader = RemoteDataLoader(lambda t: t)
        table = pa.table({"a": range(10)})

        with patch.dict("sys.modules", {"ray": fake_ray}):
            loader.start(table, batch_size=5)
            assert loader.num_batches == 2
            assert loader.is_active

            batches = list(loader)
            assert len(batches) == 2
            assert loader.batches_consumed == 2

            loader.stop()
            assert not loader.is_active
            assert loader.num_batches == 0

    def test_iterate_over_batches(self) -> None:
        fake_ray = _make_ray_mock(initialized=True)

        loader = RemoteDataLoader(lambda t: t)
        table = pa.table({"a": range(10), "b": range(10, 20)})

        with patch.dict("sys.modules", {"ray": fake_ray}):
            loader.start(table, batch_size=4)
            batches = list(loader)

        assert len(batches) == 3  # ceil(10/4) = 3
        total_rows = sum(b.num_rows for b in batches)
        assert total_rows == 10

    def test_custom_prefetch_config(self) -> None:
        config = PrefetchConfig(queue_depth=4)
        loader = RemoteDataLoader(lambda t: t, prefetch_config=config)
        assert loader._prefetch_config.queue_depth == 4

    def test_with_transform(self) -> None:
        fake_ray = _make_ray_mock(initialized=True)

        def add_col(t: pa.Table) -> pa.Table:
            return t.append_column("c", pa.array([99] * t.num_rows))

        loader = RemoteDataLoader(add_col)
        table = pa.table({"a": range(6)})

        with patch.dict("sys.modules", {"ray": fake_ray}):
            loader.start(table, batch_size=3)
            batches = list(loader)

        assert len(batches) == 2
        for b in batches:
            assert "c" in b.column_names
            assert b.column("c").to_pylist() == [99, 99, 99]


class TestCreateTorchDataLoader:
    """Test create_torch_dataloader utility."""

    def test_raises_when_ray_not_installed(self) -> None:
        table = pa.table({"a": range(5)})
        with (
            patch.dict("sys.modules", {"ray": None}),
            pytest.raises(RayRuntimeError, match="Ray is not installed"),
        ):
            list(create_torch_dataloader(table, lambda t: t))

    def test_raises_when_ray_not_initialized(self) -> None:
        fake_ray = _make_ray_mock(initialized=False)
        table = pa.table({"a": range(5)})
        with (
            patch.dict("sys.modules", {"ray": fake_ray}),
            pytest.raises(RayRuntimeError, match="not initialized"),
        ):
            list(create_torch_dataloader(table, lambda t: t))

    def test_empty_table_yields_nothing(self) -> None:
        fake_ray = _make_ray_mock(initialized=True)
        table = pa.table({"a": []})
        with patch.dict("sys.modules", {"ray": fake_ray}):
            result = list(create_torch_dataloader(table, lambda t: t))
        assert result == []

    def test_returns_batches(self) -> None:
        fake_ray = _make_ray_mock(initialized=True)
        table = pa.table({"a": range(8)})

        with patch.dict("sys.modules", {"ray": fake_ray}):
            result = list(create_torch_dataloader(table, lambda t: t, batch_size=3))

        assert len(result) == 3  # ceil(8/3) = 3

    def test_pin_memory_called(self) -> None:
        """When result has pin_memory method, it should be called."""

        class Pinnable:
            def __init__(self, table: pa.Table) -> None:
                self._table = table
                self.pinned = False

            def pin_memory(self) -> Pinnable:
                self.pinned = True
                return self

        def transform(t: pa.Table) -> Pinnable:
            return Pinnable(t)

        fake_ray = _make_ray_mock(initialized=True)
        table = pa.table({"a": range(4)})

        with patch.dict("sys.modules", {"ray": fake_ray}):
            result = list(create_torch_dataloader(table, transform, batch_size=2, pin_memory=True))

        assert len(result) == 2
        for r in result:
            assert r.pinned is True

    def test_no_pin_memory_when_disabled(self) -> None:

        class Pinnable:
            def __init__(self, table: pa.Table) -> None:
                self.pinned = False

            def pin_memory(self) -> Pinnable:
                self.pinned = True
                return self

        def transform(t: pa.Table) -> Pinnable:
            return Pinnable(t)

        fake_ray = _make_ray_mock(initialized=True)
        table = pa.table({"a": range(2)})

        with patch.dict("sys.modules", {"ray": fake_ray}):
            result = list(create_torch_dataloader(table, transform, batch_size=2, pin_memory=False))

        assert len(result) == 1
        assert result[0].pinned is False
