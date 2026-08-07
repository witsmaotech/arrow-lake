"""Blob lifecycle management — Story 7.7.

Manages S3 lifecycle rules for automatic storage tier transitions:
Standard → Infrequent Access → Glacier → Expiration.

Uses boto3 to apply lifecycle configuration to S3 buckets.
"""

from __future__ import annotations

from typing import Any

import botocore.exceptions
import structlog

from arrow_lake.config import LifecycleConfig
from arrow_lake.exceptions import ErrorCode, StorageError

logger = structlog.get_logger(__name__)

__all__ = ["BlobLifecycleManager"]

# AWS S3 pricing per GB/month (us-east-1, approximate)
_S3_PRICING: dict[str, float] = {
    "STANDARD": 0.023,
    "STANDARD_IA": 0.0125,
    "GLACIER": 0.004,
    "DEEP_ARCHIVE": 0.00099,
}


class BlobLifecycleManager:
    """Manage S3 blob lifecycle rules for automatic storage tiering.

    Applies lifecycle rules to transition objects through storage tiers:
    Standard → Infrequent Access (after standard_to_ia_days)
    Infrequent Access → Glacier (after ia_to_glacier_days)
    Glacier → Expiration (after glacier_expiration_days)

    Thumbnails and previews are excluded from lifecycle transitions.

    Args:
        config: Lifecycle configuration.
        s3_client: boto3 S3 client (None = create default client).
    """

    def __init__(
        self,
        config: LifecycleConfig,
        s3_client: Any | None = None,
    ) -> None:
        self._config = config
        if s3_client is not None:
            self._s3_client = s3_client
        else:
            import boto3
            from botocore.config import Config as BotoConfig

            # Explicit timeouts + bounded adaptive retries (was: no Config at
            # all → botocore 60s/60s defaults + legacy retry amplification).
            self._s3_client = boto3.client(
                "s3",
                config=BotoConfig(
                    connect_timeout=10,
                    read_timeout=60,
                    retries={"max_attempts": 3, "mode": "adaptive"},
                ),
            )

    @property
    def config(self) -> LifecycleConfig:
        return self._config

    def apply_lifecycle_rules(
        self,
        bucket: str,
        prefix: str = "",
    ) -> dict[str, Any]:
        """Apply S3 lifecycle rules to a bucket/prefix.

        Args:
            bucket: S3 bucket name.
            prefix: Key prefix for lifecycle rules.

        Returns:
            Dict with status and applied rules.

        Raises:
            StorageError: If lifecycle rule application fails.
        """
        if not self._config.enabled:
            return {"status": "disabled", "rules_applied": 0}

        # Warn if prefix overlaps with excluded prefixes
        for excluded in self._config.excluded_prefixes:
            exc = excluded if excluded.endswith("/") else f"{excluded}/"
            if prefix.startswith(exc) or exc.startswith(prefix):
                logger.warning(
                    "lifecycle_prefix_overlaps_excluded",
                    prefix=prefix,
                    excluded=excluded,
                    message="Lifecycle prefix overlaps with excluded prefix; "
                    "excluded objects may be affected",
                )

        rules = self._build_lifecycle_rules(prefix)

        try:
            self._s3_client.put_bucket_lifecycle_configuration(
                Bucket=bucket,
                LifecycleConfiguration={"Rules": rules},
            )
            logger.info(
                "lifecycle_rules_applied",
                bucket=bucket,
                prefix=prefix,
                rules_count=len(rules),
            )
            return {"status": "applied", "rules_applied": len(rules), "rules": rules}
        except (botocore.exceptions.ClientError, botocore.exceptions.BotoCoreError) as exc:
            raise StorageError(
                error_code=ErrorCode.LIFECYCLE_RULE_APPLY_FAILED,
                message=f"Failed to apply lifecycle rules to '{bucket}': {exc}",
            ) from exc

    def get_object_tier(self, bucket: str, key: str) -> str:
        """Get the current storage tier of an object.

        Args:
            bucket: S3 bucket name.
            key: Object key.

        Returns:
            Storage class string (e.g. 'STANDARD', 'GLACIER').

        Raises:
            StorageError: If the head request fails.
        """
        try:
            response = self._s3_client.head_object(Bucket=bucket, Key=key)
            return response.get("StorageClass", "STANDARD")
        except (botocore.exceptions.ClientError, botocore.exceptions.BotoCoreError) as exc:
            raise StorageError(
                error_code=ErrorCode.STORAGE_READ_FAILED,
                message=f"Failed to get object tier for '{key}': {exc}",
            ) from exc

    def restore_object(
        self,
        bucket: str,
        key: str,
        days: int = 7,
    ) -> dict[str, Any]:
        """Restore a Glacier-tiered object for temporary access.

        Args:
            bucket: S3 bucket name.
            key: Object key.
            days: Number of days to keep the restored copy.

        Returns:
            Dict with restoration status.

        Raises:
            StorageError: If restoration fails.
        """
        try:
            self._s3_client.restore_object(
                Bucket=bucket,
                Key=key,
                RestoreRequest={
                    "Days": days,
                    "GlacierJobParameters": {
                        "Tier": self._config.glacier_retrieval_tier,
                    },
                },
            )
            logger.info(
                "glacier_restore_initiated",
                bucket=bucket,
                key=key,
                days=days,
                tier=self._config.glacier_retrieval_tier,
            )
            return {
                "status": "initiated",
                "bucket": bucket,
                "key": key,
                "days": days,
                "tier": self._config.glacier_retrieval_tier,
            }
        except (botocore.exceptions.ClientError, botocore.exceptions.BotoCoreError) as exc:
            raise StorageError(
                error_code=ErrorCode.LIFECYCLE_RESTORE_FAILED,
                message=f"Failed to restore '{key}' from Glacier: {exc}",
            ) from exc

    def estimate_cost_savings(
        self,
        total_size_gb: int,
        current_tier: str,
        target_tier: str,
    ) -> dict[str, Any]:
        """Estimate monthly cost savings from tier transition.

        Uses approximate AWS S3 pricing for us-east-1.

        Args:
            total_size_gb: Total data size in GB.
            current_tier: Current storage class.
            target_tier: Target storage class.

        Returns:
            Dict with cost estimates and savings percentage.
        """
        current_price = _S3_PRICING.get(current_tier, 0.023)
        target_price = _S3_PRICING.get(target_tier, 0.023)

        current_monthly = total_size_gb * current_price
        target_monthly = total_size_gb * target_price
        monthly_savings = current_monthly - target_monthly

        savings_percent = (monthly_savings / current_monthly) * 100 if current_monthly > 0 else 0.0

        return {
            "total_size_gb": total_size_gb,
            "current_tier": current_tier,
            "target_tier": target_tier,
            "current_monthly_cost": round(current_monthly, 2),
            "target_monthly_cost": round(target_monthly, 2),
            "monthly_savings": round(monthly_savings, 2),
            "savings_percent": round(savings_percent, 1),
        }

    def _build_lifecycle_rules(self, prefix: str) -> list[dict[str, Any]]:
        """Build S3 lifecycle rules from configuration.

        Creates three rules for the given prefix:
        1. Standard → IA transition after standard_to_ia_days
        2. IA → Glacier transition after standard + ia_to_glacier_days
        3. Glacier → Expiration after total days

        Excluded prefixes (e.g. thumbnails/, previews/) are NOT covered by
        these rules. The caller should apply lifecycle only to non-excluded
        prefixes. S3 lifecycle Filter matches by prefix, so excluded prefixes
        that are siblings (not children) of the target prefix won't be affected.
        """
        rules = []

        # Rule 1: Standard → Infrequent Access
        rules.append(
            {
                "ID": f"arrow-lake-std-to-ia-{prefix.replace('/', '-') or 'root'}",
                "Status": "Enabled",
                "Filter": {"Prefix": prefix},
                "Transitions": [
                    {
                        "Days": self._config.standard_to_ia_days,
                        "StorageClass": "STANDARD_IA",
                    }
                ],
            }
        )

        # Rule 2: IA → Glacier
        total_to_glacier = self._config.standard_to_ia_days + self._config.ia_to_glacier_days
        glacier_rule: dict[str, Any] = {
            "ID": f"arrow-lake-ia-to-glacier-{prefix.replace('/', '-') or 'root'}",
            "Status": "Enabled",
            "Filter": {"Prefix": prefix},
            "Transitions": [
                {
                    "Days": total_to_glacier,
                    "StorageClass": "GLACIER",
                }
            ],
        }
        rules.append(glacier_rule)

        # Rule 3: Glacier → Expiration (separate rule for clarity)
        total_to_expiration = total_to_glacier + self._config.glacier_expiration_days
        rules.append(
            {
                "ID": f"arrow-lake-expiration-{prefix.replace('/', '-') or 'root'}",
                "Status": "Enabled",
                "Filter": {"Prefix": prefix},
                "Expiration": {
                    "Days": total_to_expiration,
                },
            }
        )

        return rules
