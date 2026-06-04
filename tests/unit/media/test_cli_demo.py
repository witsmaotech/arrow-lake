"""Tests for arrow-lake demo CLI command."""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from arrow_lake.cli import main
from click.testing import CliRunner

lance = pytest.importorskip("lance", reason="lance not installed")


def _make_local_runner():
    """Create a CliRunner that forces LOCAL storage backend."""
    env = dict(os.environ)
    env["ARROW_LAKE__STORAGE__BACKEND"] = "local"
    env["ARROW_LAKE__STORAGE__S3_ACCESS_KEY"] = ""
    env["ARROW_LAKE__STORAGE__S3_SECRET_KEY"] = ""
    return CliRunner(env=env)


@pytest.mark.skip(reason="Integration test — requires full runtime with DuckDB/Lance")
class TestCLIDemo:
    """Test the 'arrow-lake demo' CLI command."""

    def test_demo_help(self) -> None:
        runner = CliRunner()
        result = runner.invoke(main, ["demo", "--help"])
        assert result.exit_code == 0
        assert "demo" in result.output.lower()

    def test_demo_runs_successfully(self, tmp_path: Path) -> None:
        runner = _make_local_runner()
        result = runner.invoke(
            main, ["demo", "--base-uri", str(tmp_path / "demo"), "--no-cleanup"]
        )
        assert result.exit_code == 0, f"Demo failed: {result.output}\n{result.stderr}"
        assert "Vector Search" in result.output
        assert "SQL Analytics" in result.output
        assert "Demo completed" in result.output

    def test_demo_no_cleanup_preserves_data(self, tmp_path: Path) -> None:
        demo_dir = tmp_path / "demo_persist"
        runner = _make_local_runner()
        result = runner.invoke(
            main, ["demo", "--base-uri", str(demo_dir), "--no-cleanup"]
        )
        assert result.exit_code == 0
        assert demo_dir.exists()
