"""Tests for S3.5 --format output switching and S3.7 shared HTTP client."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import click
import pytest
from click.testing import CliRunner

from arrow_lake.cli import _get_output_format, _output_table, main


# ── S3.5: --format output ──


class TestGetOutputFormat:
    def test_default_is_table(self) -> None:
        ctx = click.Context(click.Command("test"), obj={})
        assert _get_output_format(ctx) == "table"

    def test_json_format(self) -> None:
        ctx = click.Context(click.Command("test"), obj={"format": "json"})
        assert _get_output_format(ctx) == "json"

    def test_csv_format(self) -> None:
        ctx = click.Context(click.Command("test"), obj={"format": "csv"})
        assert _get_output_format(ctx) == "csv"


class TestOutputTable:
    def test_json_output_with_data(self) -> None:
        from rich.table import Table
        import json

        table = Table()
        data = [{"Name": "test", "Value": "42"}]

        ctx = click.Context(click.Command("test"), obj={"format": "json"})
        with patch("click.echo") as mock_echo:
            _output_table(ctx, table, data=data)
            mock_echo.assert_called_once()
            output = mock_echo.call_args[0][0]
            parsed = json.loads(output)
            assert parsed == data

    def test_json_output_without_data_falls_back_to_columns(self) -> None:
        from rich.table import Table
        import json

        table = Table()
        table.add_column("Name")
        table.add_column("Value")

        ctx = click.Context(click.Command("test"), obj={"format": "json"})
        with patch("click.echo") as mock_echo:
            _output_table(ctx, table)
            output = mock_echo.call_args[0][0]
            parsed = json.loads(output)
            assert "Name" in parsed
            assert "Value" in parsed

    def test_csv_output_with_data(self) -> None:
        from rich.table import Table

        table = Table()
        data = [{"Name": "a", "Val": "1"}]

        ctx = click.Context(click.Command("test"), obj={"format": "csv"})
        with patch("click.echo") as mock_echo:
            _output_table(ctx, table, data=data)
            output = mock_echo.call_args[0][0]
            assert "Name" in output
            assert "a" in output

    def test_table_output_uses_console(self) -> None:
        from rich.table import Table

        table = Table()
        table.add_column("X")
        table.add_row("1")

        ctx = click.Context(click.Command("test"), obj={"format": "table"})
        with patch("arrow_lake.cli.console.print") as mock_print:
            _output_table(ctx, table)
            mock_print.assert_called_once_with(table)

    def test_json_output_with_explicit_data(self) -> None:
        from rich.table import Table
        import json

        table = Table()
        data = [{"col": "val"}]

        ctx = click.Context(click.Command("test"), obj={"format": "json"})
        with patch("click.echo") as mock_echo:
            _output_table(ctx, table, data=data)
            output = mock_echo.call_args[0][0]
            parsed = json.loads(output)
            assert parsed == data


class TestFormatInCLI:
    def test_catalog_list_json_format(self) -> None:
        runner = CliRunner()
        with patch("arrow_lake.Lake") as MockLake, \
             patch("arrow_lake.ArrowLakeConfig"):
            lake = MagicMock()
            lake.list_datasets.return_value = ["ds1", "ds2"]
            MockLake.return_value = lake

            result = runner.invoke(main, ["--format", "json", "catalog", "list"])
            assert result.exit_code == 0
            assert "ds1" in result.output

    def test_catalog_list_table_format(self) -> None:
        runner = CliRunner()
        with patch("arrow_lake.Lake") as MockLake, \
             patch("arrow_lake.ArrowLakeConfig"):
            lake = MagicMock()
            lake.list_datasets.return_value = ["ds1"]
            MockLake.return_value = lake

            result = runner.invoke(main, ["--format", "table", "catalog", "list"])
            assert result.exit_code == 0
            assert "ds1" in result.output


# ── S3.7: Shared HTTP client ──


class TestSharedHttpClient:
    def test_get_shared_http_client_returns_same_instance(self) -> None:
        from arrow_lake import Lake

        with patch("arrow_lake.core.http.create_http_client") as mock_create:
            mock_client = MagicMock()
            mock_create.return_value = mock_client

            lake = Lake(base_uri="./data")
            client1 = lake._get_shared_http_client()
            client2 = lake._get_shared_http_client()
            assert client1 is client2
            mock_create.assert_called_once()

    def test_get_shared_async_http_client_returns_same_instance(self) -> None:
        from arrow_lake import Lake

        with patch("arrow_lake.core.http.create_async_http_client") as mock_create:
            mock_client = MagicMock()
            mock_create.return_value = mock_client

            lake = Lake(base_uri="./data")
            client1 = lake._get_shared_async_http_client()
            client2 = lake._get_shared_async_http_client()
            assert client1 is client2
            mock_create.assert_called_once()

    def test_shutdown_closes_shared_clients(self) -> None:
        from arrow_lake import Lake

        with patch("arrow_lake.core.http.create_http_client") as mock_sync, \
             patch("arrow_lake.core.http.create_async_http_client") as mock_async:
            sync_client = MagicMock(spec=["close"])
            async_client = MagicMock(spec=["close"])
            mock_sync.return_value = sync_client
            mock_async.return_value = async_client

            lake = Lake(base_uri="./data")
            _ = lake._get_shared_http_client()
            _ = lake._get_shared_async_http_client()
            lake.shutdown()

            sync_client.close.assert_called()
            async_client.close.assert_called()
