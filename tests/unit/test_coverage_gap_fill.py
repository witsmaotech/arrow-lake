"""Coverage gap-fill tests for lineage CLI and query/__init__.py."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from arrow_lake.cli import main
from click.testing import CliRunner

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def runner() -> CliRunner:
    return CliRunner()


@pytest.fixture()
def mock_lake():
    with (
        patch("arrow_lake.Lake") as MockLake,
        patch("arrow_lake.ArrowLakeConfig") as MockConfig,
    ):
        lake = MagicMock()
        MockLake.return_value = lake
        MockConfig.from_yaml = MagicMock(return_value=None)
        yield lake


# ---------------------------------------------------------------------------
# lineage record
# ---------------------------------------------------------------------------


class TestLineageRecord:

    def test_record_success(self, runner: CliRunner, mock_lake: MagicMock) -> None:
        mock_lake.lineage_record_event.return_value = None
        result = runner.invoke(
            main,
            ["--base-uri", "/tmp", "lineage", "record", "my_ds", "append"],
        )
        assert result.exit_code == 0
        mock_lake.lineage_record_event.assert_called_once_with(
            dataset_name="my_ds",
            operation="append",
            source_datasets=[],
            transform_type=None,
            actor="cli",
            metadata={},
        )

    def test_record_with_sources(self, runner: CliRunner, mock_lake: MagicMock) -> None:
        result = runner.invoke(
            main,
            [
                "--base-uri", "/tmp",
                "lineage", "record", "ds_out", "transform",
                "--sources", "ds_a, ds_b",
                "--transform-type", "sql_etl",
                "--actor", "pipeline",
            ],
        )
        assert result.exit_code == 0
        call_kwargs = mock_lake.lineage_record_event.call_args
        assert call_kwargs.kwargs["source_datasets"] == ["ds_a", "ds_b"]
        assert call_kwargs.kwargs["transform_type"] == "sql_etl"
        assert call_kwargs.kwargs["actor"] == "pipeline"

    def test_record_with_metadata_json(self, runner: CliRunner, mock_lake: MagicMock) -> None:
        result = runner.invoke(
            main,
            [
                "--base-uri", "/tmp",
                "lineage", "record", "ds", "delete",
                "--metadata", '{"reason": "gdpr"}',
            ],
        )
        assert result.exit_code == 0
        call_kwargs = mock_lake.lineage_record_event.call_args
        assert call_kwargs.kwargs["metadata"] == {"reason": "gdpr"}

    def test_record_invalid_metadata_json(self, runner: CliRunner, mock_lake: MagicMock) -> None:
        result = runner.invoke(
            main,
            [
                "--base-uri", "/tmp",
                "lineage", "record", "ds", "update",
                "--metadata", "{bad json}",
            ],
        )
        assert result.exit_code == 1
        assert "Invalid JSON" in result.output

    def test_record_lake_raises(self, runner: CliRunner, mock_lake: MagicMock) -> None:
        mock_lake.lineage_record_event.side_effect = RuntimeError("boom")
        result = runner.invoke(
            main,
            ["--base-uri", "/tmp", "lineage", "record", "ds", "read"],
        )
        assert result.exit_code == 1
        assert "Failed to record lineage" in result.output


# ---------------------------------------------------------------------------
# lineage history
# ---------------------------------------------------------------------------


class TestLineageHistory:

    def test_history_success_dict_events(self, runner: CliRunner, mock_lake: MagicMock) -> None:
        mock_lake.lineage_history.return_value = [
            {
                "timestamp": "2025-01-01T00:00:00Z",
                "operation": "append",
                "source_datasets": ["src1"],
                "actor": "cli",
            },
        ]
        result = runner.invoke(
            main,
            ["--base-uri", "/tmp", "lineage", "history", "my_ds"],
        )
        assert result.exit_code == 0
        assert "append" in result.output

    def test_history_no_events(self, runner: CliRunner, mock_lake: MagicMock) -> None:
        mock_lake.lineage_history.return_value = []
        result = runner.invoke(
            main,
            ["--base-uri", "/tmp", "lineage", "history", "empty_ds"],
        )
        assert result.exit_code == 0
        assert "No lineage events found" in result.output

    def test_history_string_events(self, runner: CliRunner, mock_lake: MagicMock) -> None:
        mock_lake.lineage_history.return_value = ["event_str_1", "event_str_2"]
        result = runner.invoke(
            main,
            ["--base-uri", "/tmp", "lineage", "history", "raw_ds"],
        )
        assert result.exit_code == 0
        assert "event_str_1" in result.output

    def test_history_lake_raises(self, runner: CliRunner, mock_lake: MagicMock) -> None:
        mock_lake.lineage_history.side_effect = ConnectionError("refused")
        result = runner.invoke(
            main,
            ["--base-uri", "/tmp", "lineage", "history", "ds"],
        )
        assert result.exit_code == 1
        assert "Failed to get lineage history" in result.output


# ---------------------------------------------------------------------------
# lineage query
# ---------------------------------------------------------------------------


def _make_mock_result(num_rows: int, columns: list[str], rows: list[list]) -> MagicMock:
    """Build a mock pyarrow-like result for lineage_query."""
    result = MagicMock()
    result.num_rows = num_rows
    result.column_names = columns

    col_mocks: dict[str, list[MagicMock]] = {}
    for col_idx, col_name in enumerate(columns):
        cells = []
        for row in rows:
            cell = MagicMock()
            cell.as_py.return_value = row[col_idx]
            cells.append(cell)
        col_mocks[col_name] = cells

    def _column(name: str) -> list[MagicMock]:
        return col_mocks.get(name, [])

    result.column.side_effect = _column
    return result


class TestLineageQuery:

    def test_query_success(self, runner: CliRunner, mock_lake: MagicMock) -> None:
        result = _make_mock_result(
            num_rows=2,
            columns=["operation", "dataset"],
            rows=[["append", "ds1"], ["delete", "ds2"]],
        )
        mock_lake.lineage_query.return_value = result
        cli_result = runner.invoke(
            main,
            ["--base-uri", "/tmp", "lineage", "query", "SELECT * FROM lineage"],
        )
        assert cli_result.exit_code == 0
        assert "append" in cli_result.output
        assert "2" in cli_result.output

    def test_query_zero_rows(self, runner: CliRunner, mock_lake: MagicMock) -> None:
        result = _make_mock_result(
            num_rows=0,
            columns=["op"],
            rows=[],
        )
        mock_lake.lineage_query.return_value = result
        cli_result = runner.invoke(
            main,
            ["--base-uri", "/tmp", "lineage", "query", "SELECT * FROM lineage WHERE 1=0"],
        )
        assert cli_result.exit_code == 0
        assert "0 rows" in cli_result.output

    def test_query_truncates_long_values(self, runner: CliRunner, mock_lake: MagicMock) -> None:
        long_val = "x" * 100
        result = _make_mock_result(
            num_rows=1,
            columns=["data"],
            rows=[[long_val]],
        )
        mock_lake.lineage_query.return_value = result
        cli_result = runner.invoke(
            main,
            ["--base-uri", "/tmp", "lineage", "query", "SELECT data FROM lineage"],
        )
        assert cli_result.exit_code == 0
        assert "xxx" in cli_result.output and len(cli_result.output) < 500

    def test_query_null_values(self, runner: CliRunner, mock_lake: MagicMock) -> None:
        result = _make_mock_result(
            num_rows=1,
            columns=["col_a", "col_b"],
            rows=[[None, "ok"]],
        )
        mock_lake.lineage_query.return_value = result
        cli_result = runner.invoke(
            main,
            ["--base-uri", "/tmp", "lineage", "query", "SELECT col_a, col_b FROM lineage"],
        )
        assert cli_result.exit_code == 0
        assert "NULL" in cli_result.output

    def test_query_lake_raises(self, runner: CliRunner, mock_lake: MagicMock) -> None:
        mock_lake.lineage_query.side_effect = RuntimeError("query failed")
        cli_result = runner.invoke(
            main,
            ["--base-uri", "/tmp", "lineage", "query", "BAD SQL"],
        )
        assert cli_result.exit_code == 1
        assert "Lineage query failed" in cli_result.output


# ---------------------------------------------------------------------------
# query/__init__.py — lazy __getattr__ factory
# ---------------------------------------------------------------------------


class TestQueryLazyImports:

    def test_getattr_known_symbol(self) -> None:
        """Verify __getattr__ returns the correct class for a known name."""
        from arrow_lake.query import FacetCount
        from arrow_lake.query.faceted import FacetCount as DirectFacetCount

        assert FacetCount is DirectFacetCount

    def test_getattr_all_exports_resolve(self) -> None:
        """Every name in __all__ should be importable without AttributeError."""
        import arrow_lake.query as qmod

        for name in qmod.__all__:
            assert hasattr(qmod, name), f"{name!r} not found in query module"

    def test_getattr_unknown_symbol_raises(self) -> None:
        import arrow_lake.query as qmod

        with pytest.raises(AttributeError, match="has no attribute"):
            _ = qmod.DoesNotExistGadget

    def test_streaming_direct_import(self) -> None:
        """StreamingResult is imported eagerly (not via __getattr__)."""
        from arrow_lake.query import StreamingResult
        from arrow_lake.query.streaming import StreamingResult as Direct

        assert StreamingResult is Direct
