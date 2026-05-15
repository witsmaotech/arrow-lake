"""Unit tests for arrow_lake.ingest.transforms."""

from __future__ import annotations

import daft
import pytest
from arrow_lake.ingest.transforms import (
    apply_transforms,
    build_transforms,
)


@pytest.fixture
def sample_df() -> daft.DataFrame:
    """Create a small Daft DataFrame for transform testing."""
    return daft.from_pydict({
        "id": [1, 2, 3],
        "name": ["alice", "bob", "carol"],
        "score": [85.5, 92.0, 78.3],
    })


class TestBuildTransforms:
    """Tests for build_transforms() validation and construction."""

    def test_empty_specs_returns_empty_list(self) -> None:
        assert build_transforms([]) == []

    def test_unknown_op_raises(self) -> None:
        with pytest.raises(ValueError, match="Unknown transform op"):
            build_transforms([{"op": "explode"}])

    def test_rename_missing_from_raises(self) -> None:
        with pytest.raises(ValueError, match="rename requires"):
            build_transforms([{"op": "rename", "to": "b"}])

    def test_rename_missing_to_raises(self) -> None:
        with pytest.raises(ValueError, match="rename requires"):
            build_transforms([{"op": "rename", "from": "a"}])

    def test_select_missing_columns_raises(self) -> None:
        with pytest.raises(ValueError, match="select requires"):
            build_transforms([{"op": "select"}])

    def test_filter_missing_column_raises(self) -> None:
        with pytest.raises(ValueError, match="filter requires"):
            build_transforms([{"op": "filter", "op_name": ">"}])

    def test_filter_missing_op_name_raises(self) -> None:
        with pytest.raises(ValueError, match="filter requires"):
            build_transforms([{"op": "filter", "column": "score"}])

    def test_filter_unknown_op_name_raises(self) -> None:
        with pytest.raises(ValueError, match="Unknown filter op_name"):
            build_transforms([{"op": "filter", "column": "score", "op_name": "like"}])

    def test_cast_missing_column_raises(self) -> None:
        with pytest.raises(ValueError, match="cast requires"):
            build_transforms([{"op": "cast", "dtype": "int64"}])

    def test_cast_missing_dtype_raises(self) -> None:
        with pytest.raises(ValueError, match="cast requires"):
            build_transforms([{"op": "cast", "column": "score"}])

    def test_add_constant_missing_column_raises(self) -> None:
        with pytest.raises(ValueError, match="add_constant requires"):
            build_transforms([{"op": "add_constant", "value": "x"}])

    def test_unknown_dtype_raises(self) -> None:
        with pytest.raises(ValueError, match="Unknown dtype"):
            build_transforms([{"op": "cast", "column": "id", "dtype": "tensor"}])


class TestRenameTransform:
    def test_rename(self, sample_df: daft.DataFrame) -> None:
        transforms = build_transforms([{"op": "rename", "from": "name", "to": "full_name"}])
        result = apply_transforms(sample_df, transforms)
        col_names = result.column_names
        assert "full_name" in col_names
        assert "name" not in col_names


class TestSelectTransform:
    def test_select(self, sample_df: daft.DataFrame) -> None:
        transforms = build_transforms([{"op": "select", "columns": ["id", "score"]}])
        result = apply_transforms(sample_df, transforms)
        assert set(result.column_names) == {"id", "score"}


class TestFilterTransform:
    def test_filter_greater_than(self, sample_df: daft.DataFrame) -> None:
        transforms = build_transforms([
            {"op": "filter", "column": "id", "op_name": ">", "value": 1},
        ])
        result = apply_transforms(sample_df, transforms)
        count_val = result.count().to_arrow().column(0)[0].as_py()
        assert count_val == 2

    def test_filter_equals(self, sample_df: daft.DataFrame) -> None:
        transforms = build_transforms([
            {"op": "filter", "column": "name", "op_name": "==", "value": "bob"},
        ])
        result = apply_transforms(sample_df, transforms)
        count_val = result.count().to_arrow().column(0)[0].as_py()
        assert count_val == 1


class TestCastTransform:
    def test_cast_to_int32(self, sample_df: daft.DataFrame) -> None:
        transforms = build_transforms([{"op": "cast", "column": "id", "dtype": "int32"}])
        result = apply_transforms(sample_df, transforms)
        assert result.schema()["id"].dtype == daft.DataType.int32()


class TestAddConstantTransform:
    def test_add_string_constant(self, sample_df: daft.DataFrame) -> None:
        transforms = build_transforms([{"op": "add_constant", "column": "source", "value": "test"}])
        result = apply_transforms(sample_df, transforms)
        assert "source" in result.column_names

    def test_add_typed_constant(self, sample_df: daft.DataFrame) -> None:
        transforms = build_transforms([{"op": "add_constant", "column": "version", "value": 1, "dtype": "int64"}])
        result = apply_transforms(sample_df, transforms)
        assert "version" in result.column_names


class TestChainedTransforms:
    def test_rename_then_select(self, sample_df: daft.DataFrame) -> None:
        transforms = build_transforms([
            {"op": "rename", "from": "name", "to": "full_name"},
            {"op": "select", "columns": ["id", "full_name"]},
        ])
        result = apply_transforms(sample_df, transforms)
        assert set(result.column_names) == {"id", "full_name"}

    def test_add_constant_then_cast(self, sample_df: daft.DataFrame) -> None:
        transforms = build_transforms([
            {"op": "add_constant", "column": "batch", "value": 42},
            {"op": "cast", "column": "batch", "dtype": "int32"},
        ])
        result = apply_transforms(sample_df, transforms)
        assert "batch" in result.column_names
        assert result.schema()["batch"].dtype == daft.DataType.int32()


class TestApplyTransformsEmpty:
    def test_no_transforms_returns_same_df(self, sample_df: daft.DataFrame) -> None:
        result = apply_transforms(sample_df, [])
        assert result.column_names == sample_df.column_names
