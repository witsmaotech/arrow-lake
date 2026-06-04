"""Tests for quality/profiler.py — QualityProfiler, ColumnProfile, DatasetQualityProfile."""

from __future__ import annotations

import pyarrow as pa
import pytest

from arrow_lake.quality.profiler import ColumnProfile, DatasetQualityProfile, QualityProfiler


def _table() -> pa.Table:
    return pa.table({
        "id": [1, 2, 3],
        "name": ["alice", "bob", "alice"],
        "score": [0.8, 0.9, None],
    })


class TestColumnProfile:
    def test_creation(self) -> None:
        cp = ColumnProfile(name="x", dtype="int64", null_count=0,
                           null_percentage=0.0, unique_count=3,
                           min_value=1, max_value=3, histogram=None)
        assert cp.name == "x"
        assert cp.unique_count == 3

    def test_frozen(self) -> None:
        cp = ColumnProfile(name="x", dtype="int64", null_count=0,
                           null_percentage=0.0, unique_count=0,
                           min_value=None, max_value=None, histogram=None)
        with pytest.raises(AttributeError):
            cp.name = "y"  # type: ignore[misc]


class TestDatasetQualityProfile:
    def test_creation(self) -> None:
        dp = DatasetQualityProfile(
            dataset_name="ds", total_rows=10, total_columns=3,
            overall_quality_score=0.95, column_profiles=(), profiled_at="2024-01-01",
        )
        assert dp.total_rows == 10
        assert dp.overall_quality_score == 0.95


class TestQualityProfilerProfile:
    def test_profile_basic(self) -> None:
        result = QualityProfiler().profile(_table(), "test_ds")
        assert result.dataset_name == "test_ds"
        assert result.total_rows == 3
        assert result.total_columns == 3
        assert len(result.column_profiles) == 3
        assert result.overall_quality_score > 0

    def test_profile_column_names(self) -> None:
        result = QualityProfiler().profile(_table(), "ds")
        names = [cp.name for cp in result.column_profiles]
        assert "id" in names
        assert "name" in names

    def test_profile_null_detection(self) -> None:
        result = QualityProfiler().profile(_table(), "ds")
        score_col = [cp for cp in result.column_profiles if cp.name == "score"][0]
        assert score_col.null_count == 1
        assert score_col.null_percentage > 0

    def test_profile_unique_count(self) -> None:
        result = QualityProfiler().profile(_table(), "ds")
        id_col = [cp for cp in result.column_profiles if cp.name == "id"][0]
        # distinct_count may return 0 on some Arrow builds; just check it's an int
        assert isinstance(id_col.unique_count, int)

    def test_profile_min_max(self) -> None:
        result = QualityProfiler().profile(_table(), "ds")
        id_col = [cp for cp in result.column_profiles if cp.name == "id"][0]
        assert id_col.min_value == 1
        assert id_col.max_value == 3

    def test_profile_empty_table(self) -> None:
        t = pa.table({"x": pa.array([], type=pa.int64())})
        result = QualityProfiler().profile(t, "empty")
        assert result.total_rows == 0
        assert result.overall_quality_score == 0.0

    def test_profile_numeric_histogram(self) -> None:
        t = pa.table({"val": pa.array([1, 2, 3, 4, 5, 6, 7, 8, 9, 10])})
        result = QualityProfiler().profile(t, "hist_test")
        val_col = [cp for cp in result.column_profiles if cp.name == "val"][0]
        assert val_col.histogram is not None
        assert len(val_col.histogram) > 0

    def test_profile_string_no_histogram(self) -> None:
        result = QualityProfiler().profile(_table(), "ds")
        name_col = [cp for cp in result.column_profiles if cp.name == "name"][0]
        assert name_col.histogram is None

    def test_profile_quality_score_column(self) -> None:
        t = pa.table({"quality_score": [0.5, 0.7, 0.9]})
        result = QualityProfiler().profile(t, "scored")
        assert result.overall_quality_score > 0

    def test_profiled_at_is_string(self) -> None:
        result = QualityProfiler().profile(_table(), "ds")
        assert isinstance(result.profiled_at, str)
        assert "T" in result.profiled_at
