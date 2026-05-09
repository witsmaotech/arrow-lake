"""Unit tests for ray_runtime.distributed — ProcessingResult, AutoScaleConfig, _partition_table, foreach."""

from __future__ import annotations

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

# ---------------------------------------------------------------------------
# ProcessingResult
# ---------------------------------------------------------------------------


def test_processing_result_success():
    table = pa.table({"a": [1, 2, 3]})
    r = ProcessingResult(output=table, num_partitions=2, num_failed=0)
    assert r.success is True
    assert r.num_partitions == 2
    assert r.num_failed == 0
    assert r.errors == []


def test_processing_result_failure():
    r = ProcessingResult(num_partitions=3, num_failed=1, errors=["p0: boom"])
    assert r.success is False
    assert len(r.errors) == 1
    assert r.output is None


def test_processing_result_repr():
    r = ProcessingResult(num_partitions=5, num_failed=2)
    assert "partitions=5" in repr(r)
    assert "failed=2" in repr(r)


def test_processing_result_errors_is_copy():
    r = ProcessingResult(errors=["e1"])
    r.errors.append("e2")
    assert len(r.errors) == 1


# ---------------------------------------------------------------------------
# AutoScaleConfig
# ---------------------------------------------------------------------------


def test_autoscale_config_defaults():
    cfg = AutoScaleConfig()
    assert cfg.min_workers == 2
    assert cfg.max_workers == 10
    assert cfg.use_gpu is False


def test_autoscale_config_custom():
    cfg = AutoScaleConfig(min_workers=4, max_workers=20, use_gpu=True)
    assert cfg.min_workers == 4
    assert cfg.max_workers == 20
    assert cfg.use_gpu is True


def test_autoscale_config_invalid_min():
    with pytest.raises(ValueError, match="min_workers must be >= 1"):
        AutoScaleConfig(min_workers=0)


def test_autoscale_config_max_lt_min():
    with pytest.raises(ValueError, match="max_workers.*must be >=.*min_workers"):
        AutoScaleConfig(min_workers=10, max_workers=5)


# ---------------------------------------------------------------------------
# _partition_table
# ---------------------------------------------------------------------------


def test_partition_table_single():
    table = pa.table({"a": [1, 2, 3]})
    parts = _partition_table(table, 1)
    assert len(parts) == 1
    assert parts[0].num_rows == 3


def test_partition_table_empty():
    table = pa.table({"a": []})
    parts = _partition_table(table, 4)
    assert len(parts) == 1


def test_partition_table_even():
    table = pa.table({"a": range(10)})
    parts = _partition_table(table, 2)
    assert len(parts) == 2
    assert parts[0].num_rows == 5
    assert parts[1].num_rows == 5


def test_partition_table_uneven():
    table = pa.table({"a": range(7)})
    parts = _partition_table(table, 3)
    assert len(parts) == 3
    assert parts[0].num_rows == 3
    assert parts[1].num_rows == 2
    assert parts[2].num_rows == 2


def test_partition_table_more_partitions_than_rows():
    table = pa.table({"a": [1, 2]})
    parts = _partition_table(table, 10)
    assert len(parts) == 2


# ---------------------------------------------------------------------------
# foreach
# ---------------------------------------------------------------------------


def test_foreach_type_error():
    table = pa.table({"a": [1]})
    with pytest.raises(TypeError, match="fn must be callable"):
        foreach(table, None)


def test_foreach_ray_not_installed():
    table = pa.table({"a": [1]})
    with patch.dict("sys.modules", {"ray": None}):
        with pytest.raises(RayRuntimeError, match="Ray is not installed"):
            foreach(table, lambda t: t)


def test_foreach_ray_not_initialized():
    table = pa.table({"a": [1]})
    mock_ray = MagicMock()
    mock_ray.is_initialized.return_value = False
    with patch.dict("sys.modules", {"ray": mock_ray}):
        with pytest.raises(RayRuntimeError, match="Ray is not initialized"):
            foreach(table, lambda t: t)


def test_foreach_empty_table():
    table = pa.table({"a": []})
    mock_ray = MagicMock()
    mock_ray.is_initialized.return_value = True
    with patch.dict("sys.modules", {"ray": mock_ray}):
        result = foreach(table, lambda t: t, num_partitions=2)
    assert result.success is True
    assert result.num_partitions == 0


def test_foreach_with_autoscale():
    table = pa.table({"a": range(4)})
    mock_ray = MagicMock()
    mock_ray.is_initialized.return_value = True
    mock_remote = MagicMock()
    mock_ray.remote.return_value = mock_remote
    mock_ray.get.return_value = table

    with patch.dict("sys.modules", {"ray": mock_ray}):
        cfg = AutoScaleConfig(min_workers=1, max_workers=2)
        result = foreach(table, lambda t: t, num_partitions=10, autoscale=cfg)
    assert result.success is True
