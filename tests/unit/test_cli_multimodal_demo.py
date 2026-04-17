"""Tests for arrow-lake multimodal-demo CLI command."""

from __future__ import annotations

from pathlib import Path

from click.testing import CliRunner

from arrow_lake.cli import main


class TestCLIMultimodalDemo:
    """Test the 'arrow-lake multimodal-demo' CLI command."""

    def test_multimodal_demo_help(self) -> None:
        runner = CliRunner()
        result = runner.invoke(main, ["multimodal-demo", "--help"])
        assert result.exit_code == 0
        assert "multimodal" in result.output.lower()

    def test_multimodal_demo_runs_successfully(self, tmp_path: Path) -> None:
        runner = CliRunner()
        result = runner.invoke(
            main, ["multimodal-demo", "--base-uri", str(tmp_path / "mm"), "--no-cleanup"]
        )
        assert result.exit_code == 0, f"Multimodal demo failed: {result.output}"
        output = result.output.replace("\u2500", "").replace("\u2502", "").replace("\u250c", "")
        assert "Semantic Search" in output
        assert "SQL Analytics" in output
        assert "Cross-Modal" in output
        assert "Demo completed" in output
