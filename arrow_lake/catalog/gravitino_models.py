"""Gravitino model registry for ML model versioning."""

from __future__ import annotations

import threading
from dataclasses import dataclass
from typing import Any

import structlog

from arrow_lake.config.gravitino import GravitinoConfig

logger = structlog.get_logger(__name__)


@dataclass(frozen=True)
class ModelVersionInfo:
    """Information about a registered model version."""

    name: str
    version: int
    uri: str
    aliases: tuple[str, ...] = ()
    properties: tuple[tuple[str, str], ...] = ()


class GravitinoModelRegistry:
    """Register and manage ML models via Gravitino Model Catalog.

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
            from gravitino.client.gravitino_client import (
                GravitinoClient,  # type: ignore[import-untyped]
            )

            self._client = GravitinoClient(
                uri=self._config.uri,
                metalake_name=self._config.metalake,
            )
        except Exception as exc:
            logger.warning("gravitino_model_client_init_failed", error=str(exc))

    def _get_metalake(self) -> Any:
        if self._client is None:
            return None
        try:
            return self._client.load_metalake(self._config.metalake)
        except Exception as exc:
            logger.warning("gravitino_load_metalake_failed", error=str(exc))
            return None

    def register_model(
        self,
        name: str,
        comment: str = "",
        properties: dict[str, str] | None = None,
    ) -> None:
        """Register a new model in the catalog."""
        metalake = self._get_metalake()
        if metalake is None:
            return
        with self._lock:
            try:
                catalog = self._client.load_catalog("arrow_lake_lance")
                catalog.as_model_catalog().register_model(
                    schema="default",
                    name=name,
                    comment=comment,
                    properties=properties or {},
                )
                logger.info("gravitino_model_registered", name=name)
            except Exception as exc:
                logger.warning(
                    "gravitino_register_model_failed", name=name, error=str(exc)
                )

    def add_version(
        self,
        name: str,
        uri: str,
        aliases: list[str] | None = None,
    ) -> None:
        """Add a version to an existing model."""
        metalake = self._get_metalake()
        if metalake is None:
            return
        with self._lock:
            try:
                catalog = self._client.load_catalog("arrow_lake_lance")
                catalog.as_model_catalog().add_model_version(
                    schema="default",
                    name=name,
                    uri=uri,
                    aliases=aliases or [],
                )
                logger.info(
                    "gravitino_model_version_added",
                    name=name,
                    uri=uri,
                    aliases=aliases,
                )
            except Exception as exc:
                logger.warning(
                    "gravitino_add_version_failed",
                    name=name,
                    error=str(exc),
                )

    def get_latest_version(self, name: str) -> ModelVersionInfo | None:
        """Get the latest version of a model."""
        metalake = self._get_metalake()
        if metalake is None:
            return None
        with self._lock:
            try:
                catalog = self._client.load_catalog("arrow_lake_lance")
                version = catalog.as_model_catalog().get_model_version(
                    schema="default", name=name, version=-1
                )
                if version is None:
                    return None
                return ModelVersionInfo(
                    name=name,
                    version=version.version(),
                    uri=version.uri(),
                    aliases=tuple(version.aliases() or []),
                    properties=tuple(
                        sorted((version.properties() or {}).items())
                    ),
                )
            except Exception as exc:
                logger.warning(
                    "gravitino_get_latest_version_failed",
                    name=name,
                    error=str(exc),
                )
                return None

    def get_production_version(self, name: str) -> ModelVersionInfo | None:
        """Get the production alias version of a model."""
        metalake = self._get_metalake()
        if metalake is None:
            return None
        with self._lock:
            try:
                catalog = self._client.load_catalog("arrow_lake_lance")
                version = catalog.as_model_catalog().get_model_version_by_alias(
                    schema="default", name=name, alias="production"
                )
                if version is None:
                    return None
                return ModelVersionInfo(
                    name=name,
                    version=version.version(),
                    uri=version.uri(),
                    aliases=tuple(version.aliases() or []),
                    properties=tuple(
                        sorted((version.properties() or {}).items())
                    ),
                )
            except Exception as exc:
                logger.warning(
                    "gravitino_get_production_version_failed",
                    name=name,
                    error=str(exc),
                )
                return None

    def list_models(self) -> list[str]:
        """List all registered models."""
        metalake = self._get_metalake()
        if metalake is None:
            return []
        with self._lock:
            try:
                catalog = self._client.load_catalog("arrow_lake_lance")
                names = catalog.as_model_catalog().list_models("default")
                return list(names) if names else []
            except Exception as exc:
                logger.warning("gravitino_list_models_failed", error=str(exc))
                return []
