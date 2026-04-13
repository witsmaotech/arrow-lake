"""Tests for arrow_lake.testing.assertions — Story 2.7.

Tests data assertion helpers:
- assert_table_has_schema
- assert_row_count
- assert_column_values_unique
- assert_column_within_range
- assert_dataset_version
"""

from __future__ import annotations

import pyarrow as pa
import pytest
from arrow_lake.testing.assertions import (
    assert_column_values_unique,
    assert_column_within_range,
    assert_dataset_version,
    assert_row_count,
    assert_table_has_schema,
)


class TestAssertTableHasSchema:
    """Test schema matching assertion."""

    def test_matching_schema_passes(self) -> None:
        schema = pa.schema([("id", pa.int64()), ("name", pa.string())])
        table = pa.table({"id": [1, 2], "name": ["a", "b"]}, schema=schema)
        assert_table_has_schema(table, schema)

    def test_different_field_count_fails(self) -> None:
        schema_short = pa.schema([("id", pa.int64())])
        table = pa.table({"id": [1], "name": ["a"]})
        with pytest.raises(AssertionError, match="fields differ"):
            assert_table_has_schema(table, schema_short)

    def test_different_field_names_fails(self) -> None:
        schema = pa.schema([("id", pa.int64()), ("email", pa.string())])
        table = pa.table({"id": [1], "name": ["a"]})
        with pytest.raises(AssertionError, match="name"):
            assert_table_has_schema(table, schema)

    def test_different_field_types_fails(self) -> None:
        schema = pa.schema([("id", pa.string())])
        table = pa.table({"id": [1]})
        with pytest.raises(AssertionError, match="type"):
            assert_table_has_schema(table, schema)


class TestAssertRowCount:
    """Test row count assertion."""

    def test_exact_count_passes(self) -> None:
        table = pa.table({"x": range(5)})
        assert_row_count(table, expected=5)

    def test_wrong_count_fails(self) -> None:
        table = pa.table({"x": range(3)})
        with pytest.raises(AssertionError, match="expected 5, got 3"):
            assert_row_count(table, expected=5)


class TestAssertColumnValuesUnique:
    """Test uniqueness assertion."""

    def test_unique_column_passes(self) -> None:
        table = pa.table({"id": [1, 2, 3]})
        assert_column_values_unique(table, "id")

    def test_duplicate_column_fails(self) -> None:
        table = pa.table({"id": [1, 2, 2]})
        with pytest.raises(AssertionError, match="duplicate"):
            assert_column_values_unique(table, "id")

    def test_missing_column_fails(self) -> None:
        table = pa.table({"x": [1]})
        with pytest.raises(AssertionError, match="not found"):
            assert_column_values_unique(table, "nonexistent")


class TestAssertColumnWithinRange:
    """Test numeric range assertion."""

    def test_values_in_range_passes(self) -> None:
        table = pa.table({"score": [0.0, 0.5, 1.0]})
        assert_column_within_range(table, "score", min_val=0.0, max_val=1.0)

    def test_values_out_of_range_fails(self) -> None:
        table = pa.table({"score": [-0.1, 0.5, 1.0]})
        with pytest.raises(AssertionError, match=r"-0\.1"):
            assert_column_within_range(table, "score", min_val=0.0, max_val=1.0)

    def test_values_above_max_fails(self) -> None:
        table = pa.table({"score": [0.0, 0.5, 1.5]})
        with pytest.raises(AssertionError, match=r"1\.5"):
            assert_column_within_range(table, "score", min_val=0.0, max_val=1.0)


class TestAssertDatasetVersion:
    """Test dataset version assertion."""

    def test_correct_version_passes(self) -> None:

        class FakeDataset:
            def __init__(self, version: int) -> None:
                self.version = version

        assert_dataset_version(FakeDataset(3), expected_version=3)

    def test_wrong_version_fails(self) -> None:
        class FakeDataset:
            version: int = 1

        with pytest.raises(AssertionError, match="expected 5, got 1"):
            assert_dataset_version(FakeDataset(), expected_version=5)
