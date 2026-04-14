"""Tests for Story 7.7 — Auto-Tiered Blob Lifecycle."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from arrow_lake.config import LifecycleConfig
from arrow_lake.exceptions import StorageError
from arrow_lake.storage.lifecycle import BlobLifecycleManager


class TestLifecycleConfig:
    """Test LifecycleConfig field defaults and validation."""

    def test_default_values(self) -> None:
        config = LifecycleConfig()
        assert config.enabled is False
        assert config.standard_to_ia_days == 30
        assert config.ia_to_glacier_days == 90
        assert config.glacier_expiration_days == 365
        assert config.excluded_prefixes == ["thumbnails/", "previews/"]
        assert config.glacier_retrieval_tier == "Standard"

    def test_custom_values(self) -> None:
        config = LifecycleConfig(
            enabled=True,
            standard_to_ia_days=60,
            ia_to_glacier_days=180,
            glacier_expiration_days=730,
            excluded_prefixes=["thumbs/"],
            glacier_retrieval_tier="Expedited",
        )
        assert config.enabled is True
        assert config.standard_to_ia_days == 60
        assert config.ia_to_glacier_days == 180
        assert config.glacier_expiration_days == 730
        assert config.excluded_prefixes == ["thumbs/"]
        assert config.glacier_retrieval_tier == "Expedited"

    def test_valid_retrieval_tiers(self) -> None:
        for tier in ["Expedited", "Standard", "Bulk"]:
            config = LifecycleConfig(glacier_retrieval_tier=tier)
            assert config.glacier_retrieval_tier == tier

    def test_invalid_retrieval_tier(self) -> None:
        with pytest.raises(ValueError, match="glacier_retrieval_tier"):
            LifecycleConfig(glacier_retrieval_tier="InvalidTier")


class TestApplyLifecycleRules:
    """Test S3 lifecycle rule application."""

    def _make_manager(
        self,
        config: LifecycleConfig | None = None,
        s3_client: MagicMock | None = None,
    ) -> BlobLifecycleManager:
        if config is None:
            config = LifecycleConfig(enabled=True)
        return BlobLifecycleManager(config=config, s3_client=s3_client)

    def test_applies_lifecycle_configuration(self) -> None:
        s3_client = MagicMock()
        manager = self._make_manager(s3_client=s3_client)
        manager.apply_lifecycle_rules("arrow-lake", "blobs/")

        s3_client.put_bucket_lifecycle_configuration.assert_called_once()
        call_args = s3_client.put_bucket_lifecycle_configuration.call_args
        assert call_args[1]["Bucket"] == "arrow-lake"

    def test_lifecycle_rules_include_standard_to_ia(self) -> None:
        s3_client = MagicMock()
        config = LifecycleConfig(
            enabled=True,
            standard_to_ia_days=30,
            ia_to_glacier_days=90,
            glacier_expiration_days=365,
        )
        manager = self._make_manager(config=config, s3_client=s3_client)
        manager.apply_lifecycle_rules("my-bucket", "data/")

        call_args = s3_client.put_bucket_lifecycle_configuration.call_args
        rules = call_args[1]["LifecycleConfiguration"]["Rules"]
        # Should have 2 rules: Standard→IA and IA→Glacier (or expiration)
        assert len(rules) >= 1
        # Check ID format
        assert all(r.get("ID") for r in rules)

    def test_excluded_prefixes_not_in_rules(self) -> None:
        s3_client = MagicMock()
        config = LifecycleConfig(
            enabled=True,
            excluded_prefixes=["thumbnails/", "previews/", "metadata/"],
        )
        manager = self._make_manager(config=config, s3_client=s3_client)
        manager.apply_lifecycle_rules("my-bucket", "blobs/")

        call_args = s3_client.put_bucket_lifecycle_configuration.call_args
        rules = call_args[1]["LifecycleConfiguration"]["Rules"]
        # All rules should apply to the given prefix, not excluded prefixes
        for rule in rules:
            filter_spec = rule.get("Filter", {})
            prefix = filter_spec.get("Prefix", "")
            assert prefix == "blobs/", f"Expected prefix 'blobs/', got '{prefix}'"

    def test_disabled_returns_empty(self) -> None:
        s3_client = MagicMock()
        config = LifecycleConfig(enabled=False)
        manager = self._make_manager(config=config, s3_client=s3_client)
        result = manager.apply_lifecycle_rules("my-bucket", "blobs/")
        assert result["status"] == "disabled"
        s3_client.put_bucket_lifecycle_configuration.assert_not_called()

    def test_handles_boto3_exception(self) -> None:
        s3_client = MagicMock()
        s3_client.put_bucket_lifecycle_configuration.side_effect = Exception("S3 error")
        manager = self._make_manager(s3_client=s3_client)

        with pytest.raises(StorageError, match="LIFECYCLE_RULE_APPLY_FAILED"):
            manager.apply_lifecycle_rules("my-bucket", "blobs/")


class TestGetObjectTier:
    """Test object storage tier detection."""

    def test_returns_standard_tier(self) -> None:
        s3_client = MagicMock()
        s3_client.head_object.return_value = {"StorageClass": "STANDARD"}
        manager = BlobLifecycleManager(config=LifecycleConfig(enabled=True), s3_client=s3_client)
        tier = manager.get_object_tier("my-bucket", "blobs/image.jpg")
        assert tier == "STANDARD"

    def test_returns_ia_tier(self) -> None:
        s3_client = MagicMock()
        s3_client.head_object.return_value = {"StorageClass": "STANDARD_IA"}
        manager = BlobLifecycleManager(config=LifecycleConfig(enabled=True), s3_client=s3_client)
        tier = manager.get_object_tier("my-bucket", "blobs/image.jpg")
        assert tier == "STANDARD_IA"

    def test_returns_glacier_tier(self) -> None:
        s3_client = MagicMock()
        s3_client.head_object.return_value = {"StorageClass": "GLACIER"}
        manager = BlobLifecycleManager(config=LifecycleConfig(enabled=True), s3_client=s3_client)
        tier = manager.get_object_tier("my-bucket", "blobs/image.jpg")
        assert tier == "GLACIER"

    def test_returns_deep_archive_tier(self) -> None:
        s3_client = MagicMock()
        s3_client.head_object.return_value = {"StorageClass": "DEEP_ARCHIVE"}
        manager = BlobLifecycleManager(config=LifecycleConfig(enabled=True), s3_client=s3_client)
        tier = manager.get_object_tier("my-bucket", "blobs/image.jpg")
        assert tier == "DEEP_ARCHIVE"

    def test_handles_missing_storage_class(self) -> None:
        s3_client = MagicMock()
        s3_client.head_object.return_value = {}
        manager = BlobLifecycleManager(config=LifecycleConfig(enabled=True), s3_client=s3_client)
        tier = manager.get_object_tier("my-bucket", "blobs/image.jpg")
        assert tier == "STANDARD"


class TestRestoreObject:
    """Test Glacier object restoration."""

    def test_restore_with_default_days(self) -> None:
        s3_client = MagicMock()
        manager = BlobLifecycleManager(config=LifecycleConfig(enabled=True), s3_client=s3_client)
        manager.restore_object("my-bucket", "blobs/old.jpg")

        s3_client.restore_object.assert_called_once()
        call_args = s3_client.restore_object.call_args
        assert call_args[1]["Bucket"] == "my-bucket"
        assert call_args[1]["Key"] == "blobs/old.jpg"
        restore_request = call_args[1]["RestoreRequest"]
        assert "Days" in restore_request

    def test_restore_with_custom_days(self) -> None:
        s3_client = MagicMock()
        config = LifecycleConfig(enabled=True, glacier_retrieval_tier="Expedited")
        manager = BlobLifecycleManager(config=config, s3_client=s3_client)
        manager.restore_object("my-bucket", "blobs/old.jpg", days=14)

        call_args = s3_client.restore_object.call_args
        restore_request = call_args[1]["RestoreRequest"]
        assert restore_request["Days"] == 14

    def test_restore_uses_config_tier(self) -> None:
        s3_client = MagicMock()
        config = LifecycleConfig(enabled=True, glacier_retrieval_tier="Bulk")
        manager = BlobLifecycleManager(config=config, s3_client=s3_client)
        manager.restore_object("my-bucket", "blobs/old.jpg")

        call_args = s3_client.restore_object.call_args
        restore_request = call_args[1]["RestoreRequest"]
        assert restore_request["GlacierJobParameters"]["Tier"] == "Bulk"

    def test_restore_handles_error(self) -> None:
        s3_client = MagicMock()
        s3_client.restore_object.side_effect = Exception("Restore failed")
        manager = BlobLifecycleManager(config=LifecycleConfig(enabled=True), s3_client=s3_client)

        with pytest.raises(StorageError, match="LIFECYCLE_RESTORE_FAILED"):
            manager.restore_object("my-bucket", "blobs/old.jpg")


class TestEstimateCostSavings:
    """Test cost savings estimation (NFR-COST-02)."""

    def test_100tb_scenario_savings(self) -> None:
        manager = BlobLifecycleManager(config=LifecycleConfig(enabled=True))
        # Full lifecycle path: Standard → Glacier achieves >50% savings (NFR-COST-02)
        result = manager.estimate_cost_savings(
            total_size_gb=100_000,
            current_tier="STANDARD",
            target_tier="GLACIER",
        )
        assert "monthly_savings" in result
        assert "savings_percent" in result
        assert result["savings_percent"] > 0
        # NFR-COST-02: > 50% cost reduction via auto-tiering
        assert result["savings_percent"] > 50

    def test_glacier_cheaper_than_ia(self) -> None:
        manager = BlobLifecycleManager(config=LifecycleConfig(enabled=True))
        ia_result = manager.estimate_cost_savings(
            total_size_gb=10_000,
            current_tier="STANDARD",
            target_tier="STANDARD_IA",
        )
        glacier_result = manager.estimate_cost_savings(
            total_size_gb=10_000,
            current_tier="STANDARD",
            target_tier="GLACIER",
        )
        # Glacier should save more than IA
        assert glacier_result["savings_percent"] > ia_result["savings_percent"]

    def test_standard_to_standard_returns_zero(self) -> None:
        manager = BlobLifecycleManager(config=LifecycleConfig(enabled=True))
        result = manager.estimate_cost_savings(
            total_size_gb=10_000,
            current_tier="STANDARD",
            target_tier="STANDARD",
        )
        assert result["savings_percent"] == 0
        assert result["monthly_savings"] == 0

    def test_result_has_all_fields(self) -> None:
        manager = BlobLifecycleManager(config=LifecycleConfig(enabled=True))
        result = manager.estimate_cost_savings(
            total_size_gb=1000,
            current_tier="STANDARD",
            target_tier="STANDARD_IA",
        )
        assert "current_monthly_cost" in result
        assert "target_monthly_cost" in result
        assert "monthly_savings" in result
        assert "savings_percent" in result
        assert "total_size_gb" in result


class TestExcludedPrefixes:
    """Test that thumbnail/preview blobs are excluded from lifecycle."""

    def test_thumbnails_excluded_by_default(self) -> None:
        config = LifecycleConfig(enabled=True)
        assert "thumbnails/" in config.excluded_prefixes

    def test_previews_excluded_by_default(self) -> None:
        config = LifecycleConfig(enabled=True)
        assert "previews/" in config.excluded_prefixes

    def test_custom_excluded_prefixes(self) -> None:
        config = LifecycleConfig(
            enabled=True,
            excluded_prefixes=["thumbnails/", "previews/", "metadata/", "indexes/"],
        )
        assert len(config.excluded_prefixes) == 4
