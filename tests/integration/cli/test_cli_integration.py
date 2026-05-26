"""CLI integration tests — ingest command argument validation."""

from __future__ import annotations

from pathlib import Path

from click.testing import CliRunner

import pytest
from arrow_lake.cli import main


@pytest.fixture
def runner() -> CliRunner:
    return CliRunner()


class TestIngestCreateArgs:
    """Test `ingest create` argument validation."""

    def test_create_missing_file(self, runner: CliRunner) -> None:
        result = runner.invoke(
            main,
            ["--base-uri", "/tmp/test_lake", "ingest", "create", "test_ds", "/nonexistent/file.csv"],
        )
        assert result.exit_code != 0

    def test_create_no_file_arg(self, runner: CliRunner) -> None:
        result = runner.invoke(
            main,
            ["--base-uri", "/tmp/test_lake", "ingest", "create", "test_ds"],
        )
        assert result.exit_code != 0


class TestQuerySQLArgs:
    """Test `query sql` argument validation."""

    def test_query_missing_sql(self, runner: CliRunner) -> None:
        result = runner.invoke(
            main,
            ["--base-uri", "/tmp/test_lake", "query", "sql", "some_ds"],
        )
        assert result.exit_code != 0

    def test_query_sql_help(self, runner: CliRunner) -> None:
        result = runner.invoke(main, ["query", "sql", "--help"])
        assert result.exit_code == 0
        assert "sql" in result.output.lower()


class TestSearchArgs:
    """Test search subcommand argument validation."""

    def test_search_vector_help(self, runner: CliRunner) -> None:
        result = runner.invoke(main, ["search", "vector", "--help"])
        assert result.exit_code == 0

    def test_search_fts_help(self, runner: CliRunner) -> None:
        result = runner.invoke(main, ["search", "fts", "--help"])
        assert result.exit_code == 0

    def test_search_hybrid_help(self, runner: CliRunner) -> None:
        result = runner.invoke(main, ["search", "hybrid", "--help"])
        assert result.exit_code == 0
