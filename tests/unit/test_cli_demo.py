"""Tests for arrow-lake demo CLI command."""

from __future__ import annotations

from pathlib import Path

from click.testing import CliRunner

from arrow_lake.cli import main


class TestCLIDemo:
    """Test the 'arrow-lake demo' CLI command."""

    def test_demo_help(self) -> None:
        runner = CliRunner()
        result = runner.invoke(main, ["demo", "--help"])
        assert result.exit_code == 0
        assert "demo" in result.output.lower()

    def test_demo_runs_successfully(self, tmp_path: Path) -> None:
        runner = CliRunner()
        result = runner.invoke(
            main, ["demo", "--base-uri", str(tmp_path / "demo"), "--no-cleanup"]
        )
        assert result.exit_code == 0, f"Demo failed: {result.output}\n{result.stderr}"
        assert "Vector Search" in result.output
        assert "SQL Analytics" in result.output
        assert "Demo completed" in result.output

    def test_demo_no_cleanup_preserves_data(self, tmp_path: Path) -> None:
        demo_dir = tmp_path / "demo_persist"
        runner = CliRunner()
        result = runner.invoke(
            main, ["demo", "--base-uri", str(demo_dir), "--no-cleanup"]
        )
        assert result.exit_code == 0
        assert demo_dir.exists()
