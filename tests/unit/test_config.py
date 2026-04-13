"""Tests for arrow_lake.config — Story 1.3.

Tests the 4-layer config override system:
  1. Code defaults
  2. .env file
  3. Environment variables
  4. YAML config (highest priority)
"""

from __future__ import annotations

from typing import Any

import pytest
from arrow_lake.config import (
    ArrowLakeConfig,
    ComputeConfig,
    LogLevel,
    ObservabilityConfig,
    StorageBackend,
    StorageConfig,
)


class TestStorageConfig:
    """Test StorageConfig defaults and validation."""

    def test_default_backend_is_minio(self) -> None:
        config = StorageConfig()
        assert config.backend == "minio"

    def test_default_s3_endpoint(self) -> None:
        config = StorageConfig()
        assert config.s3_endpoint == "http://localhost:9000"

    def test_default_s3_bucket(self) -> None:
        config = StorageConfig()
        assert config.s3_bucket == "arrow-lake"

    def test_default_s3_region(self) -> None:
        config = StorageConfig()
        assert config.s3_region == "us-east-1"

    def test_sensitive_fields_have_empty_defaults(self) -> None:
        config = StorageConfig()
        assert config.s3_access_key == ""
        assert config.s3_secret_key == ""

    def test_custom_values_override_defaults(self) -> None:
        config = StorageConfig(
            backend="s3",
            s3_endpoint="https://s3.amazonaws.com",
            s3_bucket="prod-lake",
        )
        assert config.backend == "s3"
        assert config.s3_endpoint == "https://s3.amazonaws.com"
        assert config.s3_bucket == "prod-lake"


class TestComputeConfig:
    """Test ComputeConfig defaults and validation."""

    def test_default_gpu_disabled(self) -> None:
        config = ComputeConfig()
        assert config.gpu_enabled is False

    def test_default_ray_address(self) -> None:
        config = ComputeConfig()
        assert config.ray_address == "auto"

    def test_default_num_workers(self) -> None:
        config = ComputeConfig()
        assert config.num_workers == 2

    def test_gpu_can_be_enabled(self) -> None:
        config = ComputeConfig(gpu_enabled=True)
        assert config.gpu_enabled is True

    def test_num_workers_must_be_positive(self) -> None:
        with pytest.raises(ValueError):
            ComputeConfig(num_workers=0)


class TestObservabilityConfig:
    """Test ObservabilityConfig defaults and validation."""

    def test_default_metrics_enabled(self) -> None:
        config = ObservabilityConfig()
        assert config.metrics_enabled is True

    def test_default_metrics_port(self) -> None:
        config = ObservabilityConfig()
        assert config.metrics_port == 8000

    def test_default_log_level(self) -> None:
        config = ObservabilityConfig()
        assert config.log_level == "INFO"

    def test_default_correlation_id_is_empty(self) -> None:
        config = ObservabilityConfig()
        assert config.correlation_id == ""

    def test_metrics_port_must_be_valid(self) -> None:
        with pytest.raises(ValueError):
            ObservabilityConfig(metrics_port=0)
        with pytest.raises(ValueError):
            ObservabilityConfig(metrics_port=70000)

    def test_log_level_must_be_valid(self) -> None:
        with pytest.raises(ValueError):
            ObservabilityConfig(log_level="INVALID")


class TestArrowLakeConfig:
    """Test ArrowLakeConfig composition and defaults."""

    def test_has_sub_configs(self) -> None:
        config = ArrowLakeConfig()
        assert isinstance(config.storage, StorageConfig)
        assert isinstance(config.compute, ComputeConfig)
        assert isinstance(config.observability, ObservabilityConfig)

    def test_default_storage_values(self) -> None:
        config = ArrowLakeConfig()
        assert config.storage.backend == "minio"
        assert config.storage.s3_endpoint == "http://localhost:9000"

    def test_default_compute_values(self) -> None:
        config = ArrowLakeConfig()
        assert config.compute.gpu_enabled is False
        assert config.compute.num_workers == 2

    def test_default_observability_values(self) -> None:
        config = ArrowLakeConfig()
        assert config.observability.metrics_enabled is True
        assert config.observability.metrics_port == 8000
        assert config.observability.log_level == "INFO"


class TestEnvOverride:
    """Test environment variable overrides (layer 3)."""

    def test_env_overrides_default(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("ARROW_LAKE__STORAGE__BACKEND", "s3")
        config = ArrowLakeConfig()
        assert config.storage.backend == "s3"

    def test_env_overrides_s3_endpoint(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("ARROW_LAKE__STORAGE__S3_ENDPOINT", "https://custom.s3.com")
        config = ArrowLakeConfig()
        assert config.storage.s3_endpoint == "https://custom.s3.com"

    def test_env_overrides_num_workers(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("ARROW_LAKE__COMPUTE__NUM_WORKERS", "8")
        config = ArrowLakeConfig()
        assert config.compute.num_workers == 8

    def test_env_overrides_gpu_enabled(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("ARROW_LAKE__COMPUTE__GPU_ENABLED", "true")
        config = ArrowLakeConfig()
        assert config.compute.gpu_enabled is True

    def test_env_overrides_metrics_port(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("ARROW_LAKE__OBSERVABILITY__METRICS_PORT", "9090")
        config = ArrowLakeConfig()
        assert config.observability.metrics_port == 9090

    def test_env_overrides_log_level(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("ARROW_LAKE__OBSERVABILITY__LOG_LEVEL", "DEBUG")
        config = ArrowLakeConfig()
        assert config.observability.log_level == "DEBUG"

    def test_env_overrides_correlation_id(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("ARROW_LAKE__OBSERVABILITY__CORRELATION_ID", "trace-123")
        config = ArrowLakeConfig()
        assert config.observability.correlation_id == "trace-123"

    def test_no_env_uses_defaults(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("ARROW_LAKE__STORAGE__BACKEND", raising=False)
        config = ArrowLakeConfig()
        assert config.storage.backend == "minio"


class TestYamlOverride:
    """Test YAML config override (layer 4 — highest priority)."""

    def test_yaml_overrides_defaults(self, tmp_path: Any) -> None:
        yaml_content = """
storage:
  backend: s3
  s3_endpoint: "https://s3.amazonaws.com"
  s3_region: "eu-west-1"

compute:
  num_workers: 16

observability:
  metrics_port: 9090
  log_level: WARNING
"""
        yaml_file = tmp_path / "config.yaml"
        yaml_file.write_text(yaml_content)

        config = ArrowLakeConfig.from_yaml(str(yaml_file))
        assert config.storage.backend == "s3"
        assert config.storage.s3_endpoint == "https://s3.amazonaws.com"
        assert config.storage.s3_region == "eu-west-1"
        assert config.compute.num_workers == 16
        assert config.observability.metrics_port == 9090
        assert config.observability.log_level == "WARNING"

    def test_yaml_overrides_env(self, tmp_path: Any, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("ARROW_LAKE__STORAGE__BACKEND", "s3")
        yaml_content = """
storage:
  backend: gcs
"""
        yaml_file = tmp_path / "config.yaml"
        yaml_file.write_text(yaml_content)

        config = ArrowLakeConfig.from_yaml(str(yaml_file))
        assert config.storage.backend == "gcs"

    def test_yaml_partial_override_keeps_defaults(self, tmp_path: Any) -> None:
        yaml_content = """
observability:
  log_level: DEBUG
"""
        yaml_file = tmp_path / "config.yaml"
        yaml_file.write_text(yaml_content)

        config = ArrowLakeConfig.from_yaml(str(yaml_file))
        # Overridden
        assert config.observability.log_level == "DEBUG"
        # Kept defaults
        assert config.storage.backend == "minio"
        assert config.compute.num_workers == 2

    def test_missing_yaml_file_raises(self) -> None:
        with pytest.raises(FileNotFoundError):
            ArrowLakeConfig.from_yaml("/nonexistent/path.yaml")

    def test_from_yaml_with_dev_config(self) -> None:
        """Verify existing dev.yaml can be loaded."""
        config = ArrowLakeConfig.from_yaml("configs/dev.yaml")
        assert config.storage.backend == "minio"
        assert config.observability.log_level == "DEBUG"

    def test_from_yaml_with_prod_config(self) -> None:
        """Verify existing prod.yaml can be loaded."""
        config = ArrowLakeConfig.from_yaml("configs/prod.yaml")
        assert config.storage.backend == "s3"
        assert config.compute.gpu_enabled is True
        assert config.observability.log_level == "WARNING"


class TestFailFast:
    """Test fail-fast validation on required values."""

    def test_invalid_num_workers_raises(self) -> None:
        with pytest.raises(ValueError, match="num_workers"):
            ArrowLakeConfig(compute=ComputeConfig(num_workers=-1))

    def test_invalid_metrics_port_raises(self) -> None:
        with pytest.raises(ValueError, match="metrics_port"):
            ArrowLakeConfig(observability=ObservabilityConfig(metrics_port=99999))

    def test_invalid_log_level_raises(self) -> None:
        with pytest.raises(ValueError, match="log_level"):
            ArrowLakeConfig(observability=ObservabilityConfig(log_level="NONEXISTENT"))


class TestEnumTypes:
    """Test that enum-typed fields accept enum values."""

    def test_backend_accepts_enum(self) -> None:
        config = StorageConfig(backend=StorageBackend.S3)
        assert config.backend == StorageBackend.S3

    def test_backend_accepts_string(self) -> None:
        config = StorageConfig(backend="s3")
        assert config.backend == StorageBackend.S3

    def test_log_level_accepts_enum(self) -> None:
        config = ObservabilityConfig(log_level=LogLevel.DEBUG)
        assert config.log_level == LogLevel.DEBUG

    def test_log_level_accepts_string(self) -> None:
        config = ObservabilityConfig(log_level="DEBUG")
        assert config.log_level == LogLevel.DEBUG


class TestEnvFileLoading:
    """Test .env file loading (layer 2)."""

    def test_env_file_overrides_defaults(self, tmp_path: Any) -> None:
        env_content = "ARROW_LAKE__STORAGE__BACKEND=s3\nARROW_LAKE__COMPUTE__NUM_WORKERS=4\n"
        env_file = tmp_path / ".env"
        env_file.write_text(env_content)

        config = ArrowLakeConfig(_env_file=str(env_file))
        assert config.storage.backend == "s3"
        assert config.compute.num_workers == 4

    def test_env_file_lower_than_env_vars(
        self, tmp_path: Any, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        env_content = "ARROW_LAKE__STORAGE__BACKEND=s3\n"
        env_file = tmp_path / ".env"
        env_file.write_text(env_content)

        monkeypatch.setenv("ARROW_LAKE__STORAGE__BACKEND", "gcs")
        config = ArrowLakeConfig(_env_file=str(env_file))
        # env var (layer 3) overrides .env (layer 2)
        assert config.storage.backend == "gcs"


class TestYamlPreservesEnv:
    """Test that YAML partial override preserves env var values."""

    def test_yaml_partial_keeps_env_override(
        self, tmp_path: Any, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("ARROW_LAKE__STORAGE__BACKEND", "s3")
        monkeypatch.setenv("ARROW_LAKE__COMPUTE__NUM_WORKERS", "8")

        yaml_content = """
observability:
  log_level: DEBUG
"""
        yaml_file = tmp_path / "config.yaml"
        yaml_file.write_text(yaml_content)

        config = ArrowLakeConfig.from_yaml(str(yaml_file))
        # YAML override
        assert config.observability.log_level == "DEBUG"
        # Env var overrides preserved (not lost)
        assert config.storage.backend == "s3"
        assert config.compute.num_workers == 8
