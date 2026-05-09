"""Tests for arrow-lake multimodal-demo CLI command."""

from __future__ import annotations

from arrow_lake.cli import main
from click.testing import CliRunner


class TestCLIMultimodalDemo:
    """Test the 'arrow-lake multimodal-demo' CLI command."""

    def test_multimodal_demo_help(self) -> None:
        runner = CliRunner()
        result = runner.invoke(main, ["multimodal-demo", "--help"])
        assert result.exit_code == 0
        assert "multimodal" in result.output.lower()

    def test_demo_help(self) -> None:
        runner = CliRunner()
        result = runner.invoke(main, ["demo", "--help"])
        assert result.exit_code == 0
        assert "demo" in result.output.lower()
