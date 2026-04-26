"""Tests for lifecycle CLI commands."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from click.testing import CliRunner

from arrow_lake.cli import main


@pytest.fixture()
def runner() -> CliRunner:
    return CliRunner()


@pytest.fixture()
def mock_lake():
    with patch("arrow_lake.Lake") as MockLake, \
         patch("arrow_lake.ArrowLakeConfig") as MockConfig:
        lake = MagicMock()
        MockLake.return_value = lake
        MockConfig.from_yaml = MagicMock(return_value=None)
        config = MagicMock()
        config.lifecycle = MagicMock()
        config.lifecycle.enabled = True
        config.lifecycle.standard_to_ia_days = 30
        config.lifecycle.ia_to_glacier_days = 90
        config.lifecycle.glacier_expiration_days = 365
        config.lifecycle.excluded_prefixes = ["thumbnails/"]
        config.lifecycle.glacier_retrieval_tier = "Standard"
        config.storage.s3_bucket = "test-bucket"
        lake._config = config
        yield lake


class TestLifecycleApply:

    def test_apply_success(self, runner: CliRunner, mock_lake: MagicMock) -> None:
        mock_lake.lifecycle_apply.return_value = {"status": "applied", "rules_applied": 3}
        result = runner.invoke(main, ["--base-uri", "/tmp", "lifecycle", "apply"])
        assert result.exit_code == 0

    def test_apply_disabled(self, runner: CliRunner, mock_lake: MagicMock) -> None:
        mock_lake.lifecycle_apply.return_value = {"status": "disabled", "rules_applied": 0}
        result = runner.invoke(main, ["--base-uri", "/tmp", "lifecycle", "apply"])
        assert result.exit_code == 0

    def test_apply_with_prefix(self, runner: CliRunner, mock_lake: MagicMock) -> None:
        mock_lake.lifecycle_apply.return_value = {"status": "applied", "rules_applied": 1}
        result = runner.invoke(main, ["--base-uri", "/tmp", "lifecycle", "apply", "--prefix", "data/"])
        assert result.exit_code == 0


class TestLifecycleStatus:

    def test_status_success(self, runner: CliRunner, mock_lake: MagicMock) -> None:
        mock_lake.lifecycle_status.return_value = [
            {"key": "data/file1.parquet", "tier": "STANDARD", "size": "1024"},
            {"key": "data/file2.parquet", "tier": "STANDARD_IA", "size": "2048"},
        ]
        result = runner.invoke(main, ["--base-uri", "/tmp", "lifecycle", "status"])
        assert result.exit_code == 0

    def test_status_empty(self, runner: CliRunner, mock_lake: MagicMock) -> None:
        mock_lake.lifecycle_status.return_value = []
        result = runner.invoke(main, ["--base-uri", "/tmp", "lifecycle", "status"])
        assert result.exit_code == 0


class TestLifecycleRestore:

    def test_restore_success(self, runner: CliRunner, mock_lake: MagicMock) -> None:
        mock_lake.lifecycle_restore.return_value = {
            "status": "initiated", "tier": "Standard", "days": 7,
        }
        result = runner.invoke(main, ["--base-uri", "/tmp", "lifecycle", "restore", "data/old.parquet"])
        assert result.exit_code == 0

    def test_restore_with_days(self, runner: CliRunner, mock_lake: MagicMock) -> None:
        mock_lake.lifecycle_restore.return_value = {
            "status": "initiated", "tier": "Bulk", "days": 30,
        }
        result = runner.invoke(main, [
            "--base-uri", "/tmp", "lifecycle", "restore", "data/old.parquet", "--days", "30",
        ])
        assert result.exit_code == 0


class TestLifecycleEstimate:

    def test_estimate_success(self, runner: CliRunner, mock_lake: MagicMock) -> None:
        mock_lake.lifecycle_estimate.return_value = {
            "total_size_gb": 1000,
            "current_tier": "STANDARD",
            "target_tier": "STANDARD_IA",
            "current_monthly_cost": 23.0,
            "target_monthly_cost": 12.5,
            "monthly_savings": 10.5,
            "savings_percent": 45.7,
        }
        result = runner.invoke(main, [
            "--base-uri", "/tmp", "lifecycle", "estimate", "--size-gb", "1000",
        ])
        assert result.exit_code == 0

    def test_estimate_glacier(self, runner: CliRunner, mock_lake: MagicMock) -> None:
        mock_lake.lifecycle_estimate.return_value = {
            "total_size_gb": 500,
            "current_tier": "STANDARD",
            "target_tier": "GLACIER",
            "current_monthly_cost": 11.5,
            "target_monthly_cost": 2.0,
            "monthly_savings": 9.5,
            "savings_percent": 82.6,
        }
        result = runner.invoke(main, [
            "--base-uri", "/tmp", "lifecycle", "estimate",
            "--size-gb", "500", "--target-tier", "GLACIER",
        ])
        assert result.exit_code == 0


class TestLifecycleRules:

    def test_rules_preview(self, runner: CliRunner, mock_lake: MagicMock) -> None:
        mock_lake.lifecycle_rules.return_value = {
            "enabled": True,
            "prefix": "(root)",
            "standard_to_ia_days": 30,
            "ia_to_glacier_days": 90,
            "glacier_expiration_days": 365,
            "excluded_prefixes": ["thumbnails/"],
            "rules": [{"ID": "rule-1", "Status": "Enabled"}],
        }
        result = runner.invoke(main, ["--base-uri", "/tmp", "lifecycle", "rules"])
        assert result.exit_code == 0

    def test_rules_disabled(self, runner: CliRunner, mock_lake: MagicMock) -> None:
        mock_lake.lifecycle_rules.return_value = {
            "enabled": False, "prefix": "(root)",
            "standard_to_ia_days": 30, "ia_to_glacier_days": 90,
            "glacier_expiration_days": 365, "excluded_prefixes": [], "rules": [],
        }
        result = runner.invoke(main, ["--base-uri", "/tmp", "lifecycle", "rules"])
        assert result.exit_code == 0


class TestLifecycleConfig:

    def test_config_display(self, runner: CliRunner, mock_lake: MagicMock) -> None:
        result = runner.invoke(main, ["--base-uri", "/tmp", "lifecycle", "config"])
        assert result.exit_code == 0
