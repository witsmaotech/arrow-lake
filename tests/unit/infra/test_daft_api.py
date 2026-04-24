"""Tests for Daft DataFrame API — Story 3.7 (unit).

Tests DaftQueryEngine and LazyDaftFrame:
- Lazy evaluation (no execution until collect)
- Column projection
- Filter, sort, groupby operations
- Join
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pyarrow as pa
import pytest

pytest.importorskip("daft")

from arrow_lake.query.daft_api import DaftQueryEngine, LazyDaftFrame


@pytest.fixture()
def base_uri(tmp_path: Path) -> str:
    return str(tmp_path / "lance_data")


@pytest.fixture()
def storage(base_uri: str) -> Any:
    from arrow_lake.ingest.storage import LanceStorageManager

    mgr = LanceStorageManager(base_uri)
    # Create a test dataset
    table = pa.table(
        {
            "id": ["1", "2", "3", "4", "5"],
            "name": ["Alice", "Bob", "Carol", "Dave", "Eve"],
            "age": [25, 30, 35, 40, 45],
            "city": ["Beijing", "Shanghai", "Beijing", "Shenzhen", "Shanghai"],
        }
    )
    mgr.create_dataset("users", table)
    return mgr


@pytest.fixture()
def engine(base_uri: str) -> DaftQueryEngine:
    return DaftQueryEngine(base_uri=base_uri)


class TestDaftQueryEngine:
    """Test DaftQueryEngine initialization and loading."""

    def test_init(self, tmp_path: Path) -> None:
        engine = DaftQueryEngine(base_uri=str(tmp_path / "data"))
        assert engine.base_uri == str(tmp_path / "data")

    def test_load_returns_lazy_frame(self, engine: DaftQueryEngine, storage: Any) -> None:
        frame = engine.load("users")
        assert isinstance(frame, LazyDaftFrame)

    def test_load_with_columns(self, engine: DaftQueryEngine, storage: Any) -> None:
        frame = engine.load("users", columns=["id", "name"])
        assert isinstance(frame, LazyDaftFrame)


class TestLazyDaftFrame:
    """Test LazyDaftFrame lazy operations."""

    def test_select_returns_lazy_frame(self, engine: DaftQueryEngine, storage: Any) -> None:
        frame = engine.load("users")
        selected = frame.select("id", "name")
        assert isinstance(selected, LazyDaftFrame)

    def test_filter_returns_lazy_frame(self, engine: DaftQueryEngine, storage: Any) -> None:
        frame = engine.load("users")
        filtered = frame.filter("age > 30")
        assert isinstance(filtered, LazyDaftFrame)

    def test_sort_returns_lazy_frame(self, engine: DaftQueryEngine, storage: Any) -> None:
        frame = engine.load("users")
        sorted_frame = frame.sort("age")
        assert isinstance(sorted_frame, LazyDaftFrame)

    def test_collect_returns_arrow_table(self, engine: DaftQueryEngine, storage: Any) -> None:
        frame = engine.load("users")
        result = frame.collect()
        assert isinstance(result, pa.Table)
        assert result.num_rows == 5

    def test_collect_after_filter(self, engine: DaftQueryEngine, storage: Any) -> None:
        frame = engine.load("users")
        result = frame.filter("age > 30").collect()
        assert isinstance(result, pa.Table)
        assert result.num_rows == 3  # ages 35, 40, 45

    def test_collect_after_sort(self, engine: DaftQueryEngine, storage: Any) -> None:
        frame = engine.load("users")
        result = frame.sort("age", desc=True).collect()
        ages = result.column("age").to_pylist()
        assert ages == [45, 40, 35, 30, 25]

    def test_collect_after_select(self, engine: DaftQueryEngine, storage: Any) -> None:
        frame = engine.load("users")
        result = frame.select("name").collect()
        assert result.num_columns == 1
        assert "name" in result.column_names

    def test_chain_multiple_operations(self, engine: DaftQueryEngine, storage: Any) -> None:
        frame = engine.load("users")
        result = frame.select("name", "age", "city").filter("age >= 30").sort("age").collect()
        assert result.num_rows == 4
        ages = result.column("age").to_pylist()
        assert ages == [30, 35, 40, 45]

    def test_invalid_column_raises(self, engine: DaftQueryEngine, storage: Any) -> None:
        frame = engine.load("users")
        with pytest.raises((ValueError, Exception)):
            frame.select("nonexistent_column").collect()


class TestIdentifierValidation:
    """Test identifier validation for injection prevention."""

    def test_load_invalid_dataset_name(self, engine: DaftQueryEngine) -> None:
        with pytest.raises(ValueError, match="Invalid dataset name"):
            engine.load("../etc/passwd")

    def test_load_invalid_column_name(self, engine: DaftQueryEngine, storage: Any) -> None:
        with pytest.raises(ValueError, match="Invalid column name"):
            engine.load("users", columns=["1; DROP TABLE users"])

    def test_select_invalid_column(self, engine: DaftQueryEngine, storage: Any) -> None:
        frame = engine.load("users")
        with pytest.raises(ValueError, match="Invalid column name"):
            frame.select("col; DROP TABLE users")

    def test_sort_invalid_column(self, engine: DaftQueryEngine, storage: Any) -> None:
        frame = engine.load("users")
        with pytest.raises(ValueError, match="Invalid column name"):
            frame.sort("col; DROP TABLE")

    def test_join_invalid_column(self, engine: DaftQueryEngine, storage: Any) -> None:
        frame = engine.load("users")
        other = engine.load("users")
        with pytest.raises(ValueError, match="Invalid column name"):
            frame.join(other, on="bad;col")

    def test_join_invalid_how(self, engine: DaftQueryEngine, storage: Any) -> None:
        frame = engine.load("users")
        other = engine.load("users")
        with pytest.raises(ValueError, match="Invalid join type"):
            frame.join(other, on="id", how="cross")

    def test_groupby_invalid_column(self, engine: DaftQueryEngine, storage: Any) -> None:
        frame = engine.load("users")
        with pytest.raises(ValueError, match="Invalid column name"):
            frame.groupby("bad;col")

    def test_empty_result(self, engine: DaftQueryEngine, storage: Any) -> None:
        frame = engine.load("users")
        result = frame.filter("age > 100").collect()
        assert result.num_rows == 0
