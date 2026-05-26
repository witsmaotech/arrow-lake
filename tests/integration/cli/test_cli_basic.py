"""CLI basic smoke tests — help, version, error handling."""

from __future__ import annotations

from click.testing import CliRunner

import pytest
from arrow_lake.cli import main


@pytest.fixture
def runner() -> CliRunner:
    return CliRunner()


class TestCLIBasic:
    def test_help_exits_zero(self, runner: CliRunner) -> None:
        result = runner.invoke(main, ["--help"])
        assert result.exit_code == 0

    def test_unknown_command_exits_nonzero(self, runner: CliRunner) -> None:
        result = runner.invoke(main, ["nonexistent-command"])
        assert result.exit_code != 0

    def test_version_flag(self, runner: CliRunner) -> None:
        result = runner.invoke(main, ["--version"])
        assert result.exit_code == 0

    def test_ingest_help(self, runner: CliRunner) -> None:
        result = runner.invoke(main, ["ingest", "--help"])
        assert result.exit_code == 0

    def test_query_help(self, runner: CliRunner) -> None:
        result = runner.invoke(main, ["query", "--help"])
        assert result.exit_code == 0

    def test_search_help(self, runner: CliRunner) -> None:
        result = runner.invoke(main, ["search", "--help"])
        assert result.exit_code == 0

    def test_rag_help(self, runner: CliRunner) -> None:
        result = runner.invoke(main, ["rag", "--help"])
        assert result.exit_code == 0
