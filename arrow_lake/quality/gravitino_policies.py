"""Gravitino policy service for data governance."""

from __future__ import annotations

import threading
from typing import Any

import structlog

from arrow_lake.config.gravitino import GravitinoConfig

logger = structlog.get_logger(__name__)


class GravitinoPolicyService:
    """Manage Gravitino policies for retention, masking, etc.

    Args:
        config: Gravitino connection config.
    """

    def __init__(self, config: GravitinoConfig) -> None:
        self._config = config
        self._lock = threading.Lock()
        self._client: Any = None
        if config.enabled:
            self._init_client()

    def _init_client(self) -> None:
        try:
            from gravitino.client.gravitino_client import GravitinoClient  # type: ignore[import-untyped]

            self._client = GravitinoClient(
                uri=self._config.uri,
                metalake_name=self._config.metalake,
            )
        except Exception as exc:
            logger.warning("gravitino_policy_client_init_failed", error=str(exc))

    def _get_metalake(self) -> Any:
        if self._client is None:
            return None
        try:
            return self._client.load_metalake(self._config.metalake)
        except Exception as exc:
            logger.warning("gravitino_load_metalake_failed", error=str(exc))
            return None

    def create_retention_policy(self, name: str, days: int) -> None:
        """Create a data retention policy."""
        metalake = self._get_metalake()
        if metalake is None:
            return
        with self._lock:
            try:
                metalake.create_policy(
                    name=name,
                    policy_type="retention",
                    comment=f"Retain data for {days} days",
                    properties={"retention.days": str(days)},
                )
                logger.info("gravitino_retention_policy_created", name=name, days=days)
            except Exception as exc:
                logger.warning(
                    "gravitino_create_retention_policy_failed",
                    name=name,
                    error=str(exc),
                )

    def create_masking_policy(self, name: str, columns: list[str]) -> None:
        """Create a column masking policy."""
        metalake = self._get_metalake()
        if metalake is None:
            return
        with self._lock:
            try:
                metalake.create_policy(
                    name=name,
                    policy_type="masking",
                    comment=f"Mask columns: {', '.join(columns)}",
                    properties={
                        "masking.columns": json_list(columns),
                        "masking.function": "redact",
                    },
                )
                logger.info(
                    "gravitino_masking_policy_created", name=name, columns=columns
                )
            except Exception as exc:
                logger.warning(
                    "gravitino_create_masking_policy_failed",
                    name=name,
                    error=str(exc),
                )

    def apply_policy(self, policy: str, table: str) -> None:
        """Apply a policy to a table."""
        metalake = self._get_metalake()
        if metalake is None:
            return
        with self._lock:
            try:
                catalog = self._client.load_catalog("arrow_lake_lance")
                table_obj = catalog.as_table_catalog().load_table("default", table)
                table_obj.supports_policies().associate_policies([policy])
                logger.info("gravitino_policy_applied", policy=policy, table=table)
            except Exception as exc:
                logger.warning(
                    "gravitino_apply_policy_failed",
                    policy=policy,
                    table=table,
                    error=str(exc),
                )

    def list_policies(self) -> list[str]:
        """List all policies."""
        metalake = self._get_metalake()
        if metalake is None:
            return []
        with self._lock:
            try:
                policies = metalake.list_policies()
                return [p.name() for p in (policies or [])]
            except Exception as exc:
                logger.warning("gravitino_list_policies_failed", error=str(exc))
                return []


def json_list(items: list[str]) -> str:
    import json

    return json.dumps(items)
