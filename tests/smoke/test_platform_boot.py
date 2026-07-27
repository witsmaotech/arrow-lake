"""Platform boot smoke tests — Story 1.10.

Verifies the Arrow Lake platform can start and respond:
- SDK import and version
- Configuration defaults
- Core module imports
- Docker-dependent service checks (marked as smoke, skip without Docker)
"""

from __future__ import annotations

import pytest


class TestSDKImport:
    """Verify SDK entry point loads correctly."""

    def test_import_lake(self) -> None:
        """from arrow_lake import Lake works."""
        from arrow_lake import Lake

        assert Lake is not None

    def test_lake_version(self) -> None:
        """Lake.version() returns a non-empty string."""
        from arrow_lake import Lake

        lake = Lake()
        version = lake.version()
        assert isinstance(version, str)
        assert len(version) > 0

    def test_lake_methods_exist(self) -> None:
        """Lake has expected method stubs."""
        from arrow_lake import Lake

        lake = Lake()
        assert hasattr(lake, "ingest")
        assert hasattr(lake, "search")
        assert hasattr(lake, "catalog")
        assert hasattr(lake, "version")


class TestConfigDefaults:
    """Verify default configuration is correct."""

    def test_config_import(self) -> None:
        """ArrowLakeConfig can be imported and instantiated."""
        from arrow_lake.config import ArrowLakeConfig

        config = ArrowLakeConfig()
        assert config is not None

    def test_default_storage_backend(self) -> None:
        """Default storage backend is minio."""
        from arrow_lake.config import ArrowLakeConfig, StorageBackend

        config = ArrowLakeConfig()
        assert config.storage.backend == StorageBackend.MINIO

    def test_default_metrics_port(self) -> None:
        """Default metrics port is 8001 (changed in v1.9.x to avoid clashing with API port 8000)."""
        from arrow_lake.config import ArrowLakeConfig

        config = ArrowLakeConfig()
        assert config.observability.metrics_port == 8001

    def test_default_metrics_enabled(self) -> None:
        """Default metrics is enabled."""
        from arrow_lake.config import ArrowLakeConfig

        config = ArrowLakeConfig()
        assert config.observability.metrics_enabled is True

    def test_default_bucket(self) -> None:
        """Default S3 bucket is arrow-lake."""
        from arrow_lake.config import ArrowLakeConfig

        config = ArrowLakeConfig()
        assert config.storage.s3_bucket == "arrow-lake"

    def test_default_ray_address(self) -> None:
        """Default Ray address is auto."""
        from arrow_lake.config import ArrowLakeConfig

        config = ArrowLakeConfig()
        assert config.compute.ray_address == "auto"


class TestModuleImports:
    """Verify all core modules can be imported."""

    def test_exceptions_module(self) -> None:
        """Exceptions module loads with all error types."""
        from arrow_lake.exceptions import (
            ErrorCode,
        )

        assert len(ErrorCode) > 0

    def test_core_logging(self) -> None:
        """Core logging module loads."""
        from arrow_lake.core.logging import configure_logging, get_logger

        assert callable(configure_logging)
        assert callable(get_logger)

    def test_core_metrics(self) -> None:
        """Core metrics module loads."""
        from arrow_lake.core.metrics import REGISTRY

        assert REGISTRY is not None

    def test_core_validation(self) -> None:
        """Core validation module loads."""
        from arrow_lake.core.validation import ArrowCopyDetector

        detector = ArrowCopyDetector()
        assert detector is not None

    def test_catalog_connection_pool(self) -> None:
        """Catalog connection pool module loads."""
        from arrow_lake.catalog.connection_pool import (
            PoolMode,
        )

        assert PoolMode.READ == "read"
        assert PoolMode.WRITE == "write"

    def test_catalog_actor_import(self) -> None:
        """Catalog actor module can be imported (Ray optional)."""
        try:
            import ray  # noqa: F401
            from arrow_lake.catalog.actor import CatalogActor

            assert CatalogActor is not None
        except ImportError:
            pytest.skip("Ray not installed, skipping CatalogActor import")


class TestPrometheusEndpoint:
    """Verify Prometheus metrics endpoint is configurable."""

    def test_metrics_registry_has_epic1_metrics(self) -> None:
        """Registry contains the 3 Epic 1 metrics."""
        from arrow_lake.core.metrics import REGISTRY

        names = {m.name for m in REGISTRY.collect()}
        assert "arrow_lake_system_uptime_seconds" in names
        assert "arrow_lake_catalog_tables_total" in names
        assert "arrow_lake_catalog_queries" in names
