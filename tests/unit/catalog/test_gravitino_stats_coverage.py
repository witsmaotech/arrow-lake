"""Comprehensive tests for catalog/gravitino_stats.py — targeting uncovered paths.

Covers:
- GravitinoStatsCollector.__init__
- collect_table_stats: column metadata, row count, size estimate, error handling
- register_stats: disabled config, success, HTTP failure, property construction
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest

from arrow_lake.catalog.gravitino_stats import GravitinoStatsCollector
from arrow_lake.config.gravitino import GravitinoConfig


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_config(
    enabled: bool = True,
    uri: str = "http://gravitino:8090",
    metalake: str = "test_metalake",
) -> GravitinoConfig:
    return GravitinoConfig(
        enabled=enabled,
        uri=uri,
        metalake=metalake,
    )


# ---------------------------------------------------------------------------
# __init__
# ---------------------------------------------------------------------------


class TestGravitinoStatsCollectorInit:
    """Test collector initialization."""

    def test_headers_set(self) -> None:
        config = _make_config()
        collector = GravitinoStatsCollector(config)
        assert collector._headers["Accept"] == "application/vnd.gravitino.v1+json"
        assert collector._headers["Content-Type"] == "application/json"

    def test_config_stored(self) -> None:
        config = _make_config()
        collector = GravitinoStatsCollector(config)
        assert collector._config is config

    def test_default_catalog_and_schema(self) -> None:
        config = _make_config()
        collector = GravitinoStatsCollector(config)
        assert collector._LANCE_CATALOG == "lance-catalog"
        assert collector._DEFAULT_SCHEMA == "arrow_lake"


# ---------------------------------------------------------------------------
# collect_table_stats
# ---------------------------------------------------------------------------


class TestCollectTableStats:
    """Test table statistics collection from DuckDB."""

    @pytest.fixture()
    def collector(self) -> GravitinoStatsCollector:
        return GravitinoStatsCollector(_make_config())

    def test_basic_column_metadata(self, collector: GravitinoStatsCollector) -> None:
        mock_conn = MagicMock()
        mock_conn.execute.return_value.fetchall.return_value = [
            ("id", "INTEGER"),
            ("name", "VARCHAR"),
        ]

        stats = collector.collect_table_stats("my_table", mock_conn)

        assert stats["column_count"] == 2
        assert len(stats["columns"]) == 2
        assert stats["columns"][0] == {"name": "id", "type": "INTEGER"}
        assert stats["columns"][1] == {"name": "name", "type": "VARCHAR"}

    def test_row_count(self, collector: GravitinoStatsCollector) -> None:
        mock_conn = MagicMock()

        # First call: column metadata
        col_result = MagicMock()
        col_result.fetchall.return_value = [("id", "INTEGER")]

        # Second call: row count
        count_result = MagicMock()
        count_result.fetchone.return_value = (42,)

        mock_conn.execute.side_effect = [col_result, count_result]

        stats = collector.collect_table_stats("my_table", mock_conn)
        assert stats["row_count"] == 42

    def test_row_count_query_failure_defaults_to_zero(self, collector: GravitinoStatsCollector) -> None:
        mock_conn = MagicMock()

        col_result = MagicMock()
        col_result.fetchall.return_value = [("id", "INTEGER")]

        # Row count query fails
        count_call = MagicMock()
        count_call.fetchone.side_effect = Exception("table not found")

        mock_conn.execute.side_effect = [col_result, count_call]

        stats = collector.collect_table_stats("my_table", mock_conn)
        assert stats["row_count"] == 0

    def test_size_estimate(self, collector: GravitinoStatsCollector) -> None:
        mock_conn = MagicMock()

        col_result = MagicMock()
        col_result.fetchall.return_value = [("id", "INTEGER")]
        count_result = MagicMock()
        count_result.fetchone.return_value = (100,)
        size_result = MagicMock()
        size_result.fetchone.return_value = (1.5,)  # 1.5 MB

        mock_conn.execute.side_effect = [col_result, count_result, size_result]

        stats = collector.collect_table_stats("my_table", mock_conn)
        assert stats["size_mb"] == 1.5

    def test_size_estimate_null_row_defaults_to_zero(self, collector: GravitinoStatsCollector) -> None:
        mock_conn = MagicMock()

        col_result = MagicMock()
        col_result.fetchall.return_value = [("id", "INTEGER")]
        count_result = MagicMock()
        count_result.fetchone.return_value = (10,)
        size_result = MagicMock()
        size_result.fetchone.return_value = (None,)

        mock_conn.execute.side_effect = [col_result, count_result, size_result]

        stats = collector.collect_table_stats("my_table", mock_conn)
        assert stats["size_mb"] == 0.0

    def test_size_estimate_failure_defaults_to_zero(self, collector: GravitinoStatsCollector) -> None:
        mock_conn = MagicMock()

        col_result = MagicMock()
        col_result.fetchall.return_value = [("id", "INTEGER")]
        count_result = MagicMock()
        count_result.fetchone.return_value = (10,)
        size_result = MagicMock()
        size_result.fetchone.side_effect = Exception("no parquet metadata")

        mock_conn.execute.side_effect = [col_result, count_result, size_result]

        stats = collector.collect_table_stats("my_table", mock_conn)
        assert stats["size_mb"] == 0.0

    def test_outer_exception_returns_defaults(self, collector: GravitinoStatsCollector) -> None:
        mock_conn = MagicMock()
        mock_conn.execute.side_effect = Exception("connection lost")

        stats = collector.collect_table_stats("my_table", mock_conn)
        assert stats["row_count"] == 0
        assert stats["column_count"] == 0
        assert stats["size_mb"] == 0.0
        assert stats["columns"] == []
        assert stats["name"] == "my_table"

    def test_empty_table(self, collector: GravitinoStatsCollector) -> None:
        mock_conn = MagicMock()

        col_result = MagicMock()
        col_result.fetchall.return_value = []
        count_result = MagicMock()
        count_result.fetchone.return_value = (0,)

        mock_conn.execute.side_effect = [col_result, count_result]

        stats = collector.collect_table_stats("empty_table", mock_conn)
        assert stats["column_count"] == 0
        assert stats["row_count"] == 0

    def test_size_mb_rounded_to_two_decimals(self, collector: GravitinoStatsCollector) -> None:
        mock_conn = MagicMock()

        col_result = MagicMock()
        col_result.fetchall.return_value = [("id", "INTEGER")]
        count_result = MagicMock()
        count_result.fetchone.return_value = (10,)
        size_result = MagicMock()
        size_result.fetchone.return_value = (1.234567,)

        mock_conn.execute.side_effect = [col_result, count_result, size_result]

        stats = collector.collect_table_stats("my_table", mock_conn)
        assert stats["size_mb"] == 1.23


# ---------------------------------------------------------------------------
# register_stats
# ---------------------------------------------------------------------------


class TestRegisterStats:
    """Test statistics registration with Gravitino."""

    def test_disabled_config_is_noop(self) -> None:
        config = _make_config(enabled=False)
        collector = GravitinoStatsCollector(config)

        with patch("arrow_lake.catalog.gravitino_stats.urlopen") as mock_urlopen:
            collector.register_stats("my_table", {"row_count": 10})
            mock_urlopen.assert_not_called()

    def test_register_success(self) -> None:
        config = _make_config()
        collector = GravitinoStatsCollector(config)

        stats = {
            "row_count": 1000,
            "column_count": 5,
            "size_mb": 2.5,
            "columns": [
                {"name": "id", "type": "INTEGER"},
                {"name": "name", "type": "VARCHAR"},
            ],
        }

        with patch("arrow_lake.catalog.gravitino_stats.urlopen") as mock_urlopen:
            mock_resp = MagicMock()
            mock_resp.__enter__ = MagicMock(return_value=mock_resp)
            mock_resp.__exit__ = MagicMock(return_value=False)
            mock_urlopen.return_value = mock_resp

            collector.register_stats("my_table", stats)

            mock_urlopen.assert_called_once()
            req = mock_urlopen.call_args[0][0]
            assert req.method == "PATCH"
            assert "my_table" in req.full_url

            # Verify the request body
            body = json.loads(req.data.decode())
            assert "updates" in body
            props = {u["property"]: u["value"] for u in body["updates"]}
            assert props["stats.row_count"] == "1000"
            assert props["stats.column_count"] == "5"
            assert props["stats.size_mb"] == "2.50"
            assert props["stats.col.id.type"] == "INTEGER"
            assert props["stats.col.name.type"] == "VARCHAR"

    def test_register_correct_url_construction(self) -> None:
        config = _make_config(uri="http://my-gravitino:9090", metalake="my_ml")
        collector = GravitinoStatsCollector(config)

        with patch("arrow_lake.catalog.gravitino_stats.urlopen") as mock_urlopen:
            mock_resp = MagicMock()
            mock_resp.__enter__ = MagicMock(return_value=mock_resp)
            mock_resp.__exit__ = MagicMock(return_value=False)
            mock_urlopen.return_value = mock_resp

            collector.register_stats("my_table", {
                "row_count": 0,
                "column_count": 0,
                "size_mb": 0.0,
                "columns": [],
            })

            req = mock_urlopen.call_args[0][0]
            assert req.full_url.startswith("http://my-gravitino:9090/api/metalakes/my_ml")
            assert "lance-catalog" in req.full_url
            assert "arrow_lake" in req.full_url

    def test_register_http_failure_does_not_raise(self) -> None:
        config = _make_config()
        collector = GravitinoStatsCollector(config)

        with patch("arrow_lake.catalog.gravitino_stats.urlopen", side_effect=Exception("timeout")):
            # Should not raise
            collector.register_stats("my_table", {"row_count": 10})

    def test_register_stats_with_empty_columns(self) -> None:
        config = _make_config()
        collector = GravitinoStatsCollector(config)

        stats = {
            "row_count": 0,
            "column_count": 0,
            "size_mb": 0.0,
            "columns": [],
        }

        with patch("arrow_lake.catalog.gravitino_stats.urlopen") as mock_urlopen:
            mock_resp = MagicMock()
            mock_resp.__enter__ = MagicMock(return_value=mock_resp)
            mock_resp.__exit__ = MagicMock(return_value=False)
            mock_urlopen.return_value = mock_resp

            collector.register_stats("my_table", stats)

            body = json.loads(mock_urlopen.call_args[0][0].data.decode())
            props = {u["property"]: u["value"] for u in body["updates"]}
            # Should only have base stats, no column type properties
            assert not any(k.startswith("stats.col.") for k in props)

    def test_register_stats_missing_fields_use_defaults(self) -> None:
        config = _make_config()
        collector = GravitinoStatsCollector(config)

        stats = {}  # Missing all fields

        with patch("arrow_lake.catalog.gravitino_stats.urlopen") as mock_urlopen:
            mock_resp = MagicMock()
            mock_resp.__enter__ = MagicMock(return_value=mock_resp)
            mock_resp.__exit__ = MagicMock(return_value=False)
            mock_urlopen.return_value = mock_resp

            collector.register_stats("my_table", stats)

            body = json.loads(mock_urlopen.call_args[0][0].data.decode())
            props = {u["property"]: u["value"] for u in body["updates"]}
            assert props["stats.row_count"] == "0"
            assert props["stats.column_count"] == "0"
            assert props["stats.size_mb"] == "0.00"

    def test_register_stats_column_with_empty_name_skipped(self) -> None:
        config = _make_config()
        collector = GravitinoStatsCollector(config)

        stats = {
            "row_count": 0,
            "column_count": 1,
            "size_mb": 0.0,
            "columns": [{"name": "", "type": "INTEGER"}],
        }

        with patch("arrow_lake.catalog.gravitino_stats.urlopen") as mock_urlopen:
            mock_resp = MagicMock()
            mock_resp.__enter__ = MagicMock(return_value=mock_resp)
            mock_resp.__exit__ = MagicMock(return_value=False)
            mock_urlopen.return_value = mock_resp

            collector.register_stats("my_table", stats)

            body = json.loads(mock_urlopen.call_args[0][0].data.decode())
            props = {u["property"]: u["value"] for u in body["updates"]}
            # Empty column name should not generate a stats.col. property
            assert not any(k.startswith("stats.col.") for k in props)
