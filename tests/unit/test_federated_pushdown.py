"""Unit tests for v1.5.1 predicate pushdown — Phase 3.1."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from arrow_lake.query.federated_engine import FederatedQueryEngine


class TestExtractSimpleFilters:
    """Test _extract_simple_filters regex-based WHERE clause parser."""

    def setup_method(self) -> None:
        self.engine = FederatedQueryEngine.__new__(FederatedQueryEngine)

    def test_basic_equality(self) -> None:
        result = self.engine._extract_simple_filters("SELECT * FROM t WHERE status = 1")
        assert result == {"status": "status = 1"}

    def test_range_and_equality(self) -> None:
        result = self.engine._extract_simple_filters(
            "SELECT * FROM t WHERE x > 10 AND y = 20"
        )
        assert "x" in result
        assert "y" in result
        assert result["x"] == "x > 10"
        assert result["y"] == "y = 20"

    def test_string_value(self) -> None:
        result = self.engine._extract_simple_filters(
            "SELECT * FROM t WHERE name = 'alice'"
        )
        assert result == {"name": "name = 'alice'"}

    def test_no_where_clause(self) -> None:
        result = self.engine._extract_simple_filters("SELECT * FROM t")
        assert result == {}

    def test_or_clause_skipped(self) -> None:
        result = self.engine._extract_simple_filters(
            "SELECT * FROM t WHERE x > 10 OR y = 20"
        )
        assert result == {}

    def test_nested_parens_skipped(self) -> None:
        result = self.engine._extract_simple_filters(
            "SELECT * FROM t WHERE (x > 10)"
        )
        assert result == {}

    def test_where_with_order_by(self) -> None:
        result = self.engine._extract_simple_filters(
            "SELECT * FROM t WHERE x > 5 ORDER BY y"
        )
        assert result == {"x": "x > 5"}

    def test_where_with_limit(self) -> None:
        result = self.engine._extract_simple_filters(
            "SELECT * FROM t WHERE status = 1 LIMIT 100"
        )
        assert result == {"status": "status = 1"}

    def test_greater_equal(self) -> None:
        result = self.engine._extract_simple_filters(
            "SELECT * FROM t WHERE age >= 18"
        )
        assert result == {"age": "age >= 18"}

    def test_not_equal(self) -> None:
        result = self.engine._extract_simple_filters(
            "SELECT * FROM t WHERE status != 0"
        )
        assert result == {"status": "status != 0"}

    def test_boolean_value(self) -> None:
        result = self.engine._extract_simple_filters(
            "SELECT * FROM t WHERE active = TRUE"
        )
        assert result == {"active": "active = TRUE"}

    def test_multiple_columns(self) -> None:
        result = self.engine._extract_simple_filters(
            "SELECT * FROM t WHERE region = 'US' AND year > 2020 AND category = 'A'"
        )
        assert len(result) == 3
        assert "region" in result
        assert "year" in result
        assert "category" in result


class TestLoadDatasetWithFilter:
    """Test load_dataset accepts optional where parameter."""

    @patch.object(FederatedQueryEngine, "resolve_table")
    @patch("daft.read_lance")
    def test_where_passed_to_dataframe(self, mock_read_lance: MagicMock, mock_resolve: MagicMock) -> None:
        mock_resolve.return_value = MagicMock(
            location="s3://bucket/ds", format="lance", columns=[{"name": "x"}, {"name": "y"}],
        )
        mock_df = MagicMock()
        mock_read_lance.return_value = mock_df
        mock_df.where.return_value = mock_df

        engine = FederatedQueryEngine(MagicMock())
        result = engine.load_dataset("test_ds", where="x > 10")

        mock_read_lance.assert_called_once_with("s3://bucket/ds")
        mock_df.where.assert_called_once_with("x > 10")

    @patch.object(FederatedQueryEngine, "resolve_table")
    @patch("daft.read_parquet")
    def test_no_where_skips_filter(self, mock_read_parquet: MagicMock, mock_resolve: MagicMock) -> None:
        mock_resolve.return_value = MagicMock(
            location="s3://bucket/ds", format="parquet", columns=[],
        )
        mock_df = MagicMock()
        mock_read_parquet.return_value = mock_df

        engine = FederatedQueryEngine(MagicMock())
        result = engine.load_dataset("test_ds")

        mock_df.where.assert_not_called()

    @patch.object(FederatedQueryEngine, "resolve_table")
    @patch("daft.read_iceberg")
    def test_iceberg_reader(self, mock_read_iceberg: MagicMock, mock_resolve: MagicMock) -> None:
        mock_resolve.return_value = MagicMock(
            location="s3://warehouse/db/table", format="iceberg", columns=[],
        )
        mock_df = MagicMock()
        mock_read_iceberg.return_value = mock_df

        engine = FederatedQueryEngine(MagicMock())
        result = engine.load_dataset("iceberg_table")

        mock_read_iceberg.assert_called_once_with("s3://warehouse/db/table")


class TestCrossCatalogQueryPushdown:
    """Test cross_catalog_query extracts and pushes filters per table."""

    @patch.object(FederatedQueryEngine, "load_dataset")
    @patch.object(FederatedQueryEngine, "resolve_table")
    def test_pushdown_matches_table_columns(self, mock_resolve: MagicMock, mock_load: MagicMock) -> None:
        config = MagicMock()
        config.federated_query_max_rows = 100000
        engine = FederatedQueryEngine(config)

        # Table A has columns x, y; Table B has columns a, b
        mock_resolve.side_effect = [
            MagicMock(location="s3://a", format="lance", columns=[{"name": "x"}, {"name": "y"}]),
            MagicMock(location="s3://b", format="lance", columns=[{"name": "a"}, {"name": "b"}]),
        ]
        mock_df_a = MagicMock()
        mock_df_a.to_arrow.return_value = __import__("pyarrow").table({"x": [1], "y": [2]})
        mock_df_b = MagicMock()
        mock_df_b.to_arrow.return_value = __import__("pyarrow").table({"a": [3], "b": [4]})
        mock_load.side_effect = [mock_df_a, mock_df_b]

        sql = "SELECT * FROM ta JOIN tb ON ta.x = tb.a WHERE x > 5 AND a = 3"
        engine._validate_sql = MagicMock()

        engine.cross_catalog_query(
            [("lance-catalog.arrow_lake.table_a", "ta"),
             ("lance-catalog.arrow_lake.table_b", "tb")],
            sql,
        )

        # Verify load_dataset called with table-specific filters
        calls = mock_load.call_args_list
        # First table: x is in its columns, so filter "x > 5" should be pushed
        first_where = calls[0].kwargs.get("where")
        assert first_where is not None
        assert "x > 5" in first_where

        # Second table: a is in its columns, so filter "a = 3" should be pushed
        second_where = calls[1].kwargs.get("where")
        assert second_where is not None
        assert "a = 3" in second_where

    @patch.object(FederatedQueryEngine, "load_dataset")
    @patch.object(FederatedQueryEngine, "resolve_table")
    def test_pushdown_disabled(self, mock_resolve: MagicMock, mock_load: MagicMock) -> None:
        config = MagicMock()
        config.federated_query_max_rows = 100000
        engine = FederatedQueryEngine(config)
        mock_resolve.return_value = MagicMock(
            location="s3://a", format="lance", columns=[{"name": "x"}],
        )
        mock_df = MagicMock()
        mock_df.to_arrow.return_value = __import__("pyarrow").table({"x": [1]})
        mock_load.return_value = mock_df

        engine._validate_sql = MagicMock()

        engine.cross_catalog_query(
            [("test.table", "t")],
            "SELECT * FROM t WHERE x > 5",
            pushdown_filters=False,
        )

        # With pushdown disabled, load_dataset should be called without where
        call = mock_load.call_args_list[0]
        assert call.kwargs.get("where") is None
