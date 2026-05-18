"""Tests for Daft DataFrame API — Story 3.7 (unit).

Tests DaftQueryEngine and LazyDaftFrame:
- Lazy evaluation (no execution until collect)
- Column projection
- Filter, sort, groupby operations
- Join
- Security hardening (SQL injection, identifier validation, collect limit)
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import daft
import pyarrow as pa
import pytest

pytest.importorskip("daft")
pytest.importorskip("lance", reason="lance not installed")

from arrow_lake.query.daft_api import DaftQueryEngine, LazyDaftFrame


@pytest.fixture()
def base_uri(tmp_path: Path) -> str:
    return str(tmp_path / "lance_data")


@pytest.fixture()
def storage(base_uri: str) -> Any:
    from arrow_lake.ingest.storage import LanceStorageManager

    mgr = LanceStorageManager(base_uri)
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

    def test_repr(self, tmp_path: Path) -> None:
        engine = DaftQueryEngine(base_uri=str(tmp_path / "data"))
        assert "DaftQueryEngine" in repr(engine)
        assert str(tmp_path / "data") in repr(engine)

    def test_load_returns_lazy_frame(self, engine: DaftQueryEngine, storage: Any) -> None:
        frame = engine.load("users")
        assert isinstance(frame, LazyDaftFrame)

    def test_load_with_columns(self, engine: DaftQueryEngine, storage: Any) -> None:
        frame = engine.load("users", columns=["id", "name"])
        assert isinstance(frame, LazyDaftFrame)

    def test_load_nonexistent_dataset(self, engine: DaftQueryEngine) -> None:
        with pytest.raises(FileNotFoundError, match="not found"):
            engine.load("nonexistent")

    def test_load_empty_dataset_name(self, engine: DaftQueryEngine) -> None:
        with pytest.raises(ValueError, match="Invalid dataset name"):
            engine.load("")


class TestLazyDaftFrame:
    """Test LazyDaftFrame lazy operations."""

    def test_repr(self, engine: DaftQueryEngine, storage: Any) -> None:
        frame = engine.load("users")
        r = repr(frame)
        assert "LazyDaftFrame" in r
        assert "columns=" in r

    def test_select_returns_lazy_frame(self, engine: DaftQueryEngine, storage: Any) -> None:
        frame = engine.load("users")
        selected = frame.select("id", "name")
        assert isinstance(selected, LazyDaftFrame)

    def test_filter_returns_lazy_frame(self, engine: DaftQueryEngine, storage: Any) -> None:
        frame = engine.load("users")
        filtered = frame.filter(daft.col("age") > 30)
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
        result = frame.filter(daft.col("age") > 30).collect()
        assert isinstance(result, pa.Table)
        assert result.num_rows == 3

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
        result = (
            frame.select("name", "age", "city")
            .filter(daft.col("age") >= 30)
            .sort("age")
            .collect()
        )
        assert result.num_rows == 4
        ages = result.column("age").to_pylist()
        assert ages == [30, 35, 40, 45]

    def test_limit_and_offset(self, engine: DaftQueryEngine, storage: Any) -> None:
        frame = engine.load("users")
        result = frame.sort("age").offset(2).limit(2).collect()
        assert result.num_rows == 2
        ages = result.column("age").to_pylist()
        assert ages == [35, 40]

    def test_groupby_sum(self, engine: DaftQueryEngine, storage: Any) -> None:
        frame = engine.load("users")
        result = frame.groupby("city").agg(daft.col("age").sum()).collect()
        assert isinstance(result, pa.Table)
        assert result.num_rows == 3

    def test_groupby_count(self, engine: DaftQueryEngine, storage: Any) -> None:
        frame = engine.load("users")
        result = frame.groupby("city").count().collect()
        assert isinstance(result, pa.Table)

    def test_groupby_agg(self, engine: DaftQueryEngine, storage: Any) -> None:
        frame = engine.load("users")
        result = frame.groupby("city").agg(
            daft.col("age").sum().alias("total_age"),
        ).collect()
        assert isinstance(result, pa.Table)

    def test_distinct(self, engine: DaftQueryEngine, storage: Any) -> None:
        frame = engine.load("users")
        result = frame.select("city").distinct().collect()
        cities = set(result.column("city").to_pylist())
        assert cities == {"Beijing", "Shanghai", "Shenzhen"}

    def test_count_rows(self, engine: DaftQueryEngine, storage: Any) -> None:
        frame = engine.load("users")
        assert frame.count_rows() == 5

    def test_with_column(self, engine: DaftQueryEngine, storage: Any) -> None:
        frame = engine.load("users")
        result = frame.with_column("double_age", daft.col("age") * 2).collect()
        assert "double_age" in result.column_names

    def test_exclude(self, engine: DaftQueryEngine, storage: Any) -> None:
        frame = engine.load("users")
        result = frame.exclude("id").collect()
        assert "id" not in result.column_names
        assert "name" in result.column_names

    def test_drop_null(self, engine: DaftQueryEngine, storage: Any) -> None:
        frame = engine.load("users")
        result = frame.drop_null().collect()
        assert isinstance(result, pa.Table)

    def test_fill_null(self, engine: DaftQueryEngine, storage: Any) -> None:
        frame = engine.load("users")
        result = frame.fill_null(0).collect()
        assert isinstance(result, pa.Table)

    def test_join(self, engine: DaftQueryEngine, storage: Any) -> None:
        frame = engine.load("users")
        other = engine.load("users")
        result = frame.join(other, on="id", how="inner").collect()
        assert result.num_rows == 5

    def test_invalid_column_raises(self, engine: DaftQueryEngine, storage: Any) -> None:
        frame = engine.load("users")
        with pytest.raises((ValueError, Exception)):
            frame.select("nonexistent_column").collect()

    def test_offset_negative(self, engine: DaftQueryEngine, storage: Any) -> None:
        frame = engine.load("users")
        with pytest.raises(ValueError, match="offset must be >= 0"):
            frame.offset(-1)

    def test_limit_zero(self, engine: DaftQueryEngine, storage: Any) -> None:
        frame = engine.load("users")
        with pytest.raises(ValueError, match="limit must be >= 1"):
            frame.limit(0)

    def test_sample_fraction_validation(self, engine: DaftQueryEngine, storage: Any) -> None:
        frame = engine.load("users")
        with pytest.raises(ValueError, match="fraction must be in"):
            frame.sample(fraction=0.0)
        with pytest.raises(ValueError, match="fraction must be in"):
            frame.sample(fraction=1.5)

    def test_sample_size_validation(self, engine: DaftQueryEngine, storage: Any) -> None:
        frame = engine.load("users")
        with pytest.raises(ValueError, match="size must be >= 1"):
            frame.sample(size=0)


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

    def test_exclude_invalid_column(self, engine: DaftQueryEngine, storage: Any) -> None:
        frame = engine.load("users")
        with pytest.raises(ValueError, match="Invalid column name"):
            frame.exclude("bad;col")

    def test_drop_null_invalid_column(self, engine: DaftQueryEngine, storage: Any) -> None:
        frame = engine.load("users")
        with pytest.raises(ValueError, match="Invalid column name"):
            frame.drop_null("bad;col")

    def test_distinct_invalid_column(self, engine: DaftQueryEngine, storage: Any) -> None:
        frame = engine.load("users")
        with pytest.raises(ValueError, match="Invalid column name"):
            frame.distinct("bad;col")

    def test_explode_invalid_column(self, engine: DaftQueryEngine, storage: Any) -> None:
        frame = engine.load("users")
        with pytest.raises(ValueError, match="Invalid column name"):
            frame.explode("bad;col")

    def test_with_column_invalid_name(self, engine: DaftQueryEngine, storage: Any) -> None:
        frame = engine.load("users")
        with pytest.raises(ValueError, match="Invalid column name"):
            frame.with_column("bad;name", daft.col("age"))

    def test_with_columns_invalid_name(self, engine: DaftQueryEngine, storage: Any) -> None:
        frame = engine.load("users")
        with pytest.raises(ValueError, match="Invalid column name"):
            frame.with_columns({"bad;name": daft.col("age")})

    def test_fill_null_invalid_column(self, engine: DaftQueryEngine, storage: Any) -> None:
        frame = engine.load("users")
        with pytest.raises(ValueError, match="Invalid column name"):
            frame.fill_null(0, column="bad;col")


class TestSqlInjectionPrevention:
    """Test SQL injection prevention in sql() method."""

    def test_sql_drop_table_blocked(self, engine: DaftQueryEngine, storage: Any) -> None:
        frame = engine.load("users")
        with pytest.raises(ValueError, match="forbidden"):
            frame.sql("DROP TABLE users")

    def test_sql_delete_blocked(self, engine: DaftQueryEngine, storage: Any) -> None:
        frame = engine.load("users")
        with pytest.raises(ValueError, match="forbidden"):
            frame.sql("DELETE FROM self WHERE id = '1'")

    def test_sql_insert_blocked(self, engine: DaftQueryEngine, storage: Any) -> None:
        frame = engine.load("users")
        with pytest.raises(ValueError, match="forbidden"):
            frame.sql("INSERT INTO self VALUES ('6', 'Hack', 0, 'Nowhere')")

    def test_sql_update_blocked(self, engine: DaftQueryEngine, storage: Any) -> None:
        frame = engine.load("users")
        with pytest.raises(ValueError, match="forbidden"):
            frame.sql("UPDATE self SET name = 'hacked'")

    def test_sql_alter_blocked(self, engine: DaftQueryEngine, storage: Any) -> None:
        frame = engine.load("users")
        with pytest.raises(ValueError, match="forbidden"):
            frame.sql("ALTER TABLE self ADD COLUMN hacked TEXT")

    def test_sql_create_blocked(self, engine: DaftQueryEngine, storage: Any) -> None:
        frame = engine.load("users")
        with pytest.raises(ValueError, match="forbidden"):
            frame.sql("CREATE TABLE evil AS SELECT * FROM self")

    def test_sql_truncate_blocked(self, engine: DaftQueryEngine, storage: Any) -> None:
        frame = engine.load("users")
        with pytest.raises(ValueError, match="forbidden"):
            frame.sql("TRUNCATE TABLE self")

    def test_sql_grant_blocked(self, engine: DaftQueryEngine, storage: Any) -> None:
        frame = engine.load("users")
        with pytest.raises(ValueError, match="forbidden"):
            frame.sql("GRANT ALL ON self TO public")

    def test_sql_empty_query_rejected(self, engine: DaftQueryEngine, storage: Any) -> None:
        frame = engine.load("users")
        with pytest.raises(ValueError, match="empty"):
            frame.sql("")

    def test_sql_whitespace_only_rejected(self, engine: DaftQueryEngine, storage: Any) -> None:
        frame = engine.load("users")
        with pytest.raises(ValueError, match="empty"):
            frame.sql("   \n\t  ")

    def test_sql_too_long_rejected(self, engine: DaftQueryEngine, storage: Any) -> None:
        frame = engine.load("users")
        with pytest.raises(ValueError, match="too long"):
            frame.sql("SELECT * FROM self WHERE " + "x" * 10_001)

    def test_sql_select_allowed(self, engine: DaftQueryEngine, storage: Any) -> None:
        frame = engine.load("users")
        result = frame.sql("SELECT * FROM self LIMIT 1").collect()
        assert isinstance(result, pa.Table)


class TestCollectMaxRows:
    """Test collect() max_rows safety cap."""

    def test_collect_truncates_at_max_rows(self, engine: DaftQueryEngine, storage: Any) -> None:
        frame = engine.load("users")
        result = frame.collect(max_rows=2)
        assert result.num_rows == 2

    def test_collect_no_truncate_when_within_limit(self, engine: DaftQueryEngine, storage: Any) -> None:
        frame = engine.load("users")
        result = frame.collect(max_rows=100)
        assert result.num_rows == 5

    def test_collect_zero_disables_limit(self, engine: DaftQueryEngine, storage: Any) -> None:
        frame = engine.load("users")
        result = frame.collect(max_rows=0)
        assert result.num_rows == 5


class TestPivotValidation:
    """Test pivot parameter validation."""

    def test_pivot_invalid_agg_fn(self, engine: DaftQueryEngine, storage: Any) -> None:
        frame = engine.load("users")
        with pytest.raises(ValueError, match="Invalid agg_fn"):
            frame.pivot("city", "city", "age", agg_fn="median")

    def test_pivot_invalid_group_by_string(self, engine: DaftQueryEngine, storage: Any) -> None:
        frame = engine.load("users")
        with pytest.raises(ValueError, match="Invalid group_by"):
            frame.pivot("bad;col", "city", "age", agg_fn="sum")


class TestUnpivotValidation:
    """Test unpivot parameter validation."""

    def test_unpivot_invalid_variable_name(self, engine: DaftQueryEngine, storage: Any) -> None:
        frame = engine.load("users")
        with pytest.raises(ValueError, match="Invalid variable_name"):
            frame.unpivot("id", variable_name="bad;name")

    def test_unpivot_invalid_value_name(self, engine: DaftQueryEngine, storage: Any) -> None:
        frame = engine.load("users")
        with pytest.raises(ValueError, match="Invalid value_name"):
            frame.unpivot("id", value_name="bad;name")

    def test_unpivot_invalid_id_column(self, engine: DaftQueryEngine, storage: Any) -> None:
        frame = engine.load("users")
        with pytest.raises(ValueError, match="Invalid ids column"):
            frame.unpivot("bad;col")


class TestErrorSanitization:
    """Test that error messages don't leak internal details."""

    def test_load_not_found_no_path(self, engine: DaftQueryEngine) -> None:
        with pytest.raises(FileNotFoundError) as exc_info:
            engine.load("missing")
        msg = str(exc_info.value)
        assert "missing" in msg
        assert ".lance" not in msg
        assert "/" not in msg

    def test_load_error_no_internal_details(self, engine: DaftQueryEngine) -> None:
        """Missing dataset raises FileNotFoundError without internal path details."""
        with pytest.raises(FileNotFoundError) as exc_info:
            engine.load("missing")
        msg = str(exc_info.value)
        assert "missing" in msg
        assert ".lance" not in msg
