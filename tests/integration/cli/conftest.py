"""CLI integration test fixtures."""

from __future__ import annotations

from pathlib import Path

from click.testing import CliRunner

import pytest


@pytest.fixture
def runner() -> CliRunner:
    return CliRunner()


@pytest.fixture
def lake_path(tmp_path: Path) -> str:
    return str(tmp_path / "test_lake")
