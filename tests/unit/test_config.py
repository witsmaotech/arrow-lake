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
    DecodeConfig,
    EmbeddingConfig,
    HttpConfig,
    LogLevel,
    MediaConfig,
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


class TestHttpConfig:
    """Test HttpConfig defaults and validation (Story 3.2)."""

    def test_default_timeout(self) -> None:
        config = HttpConfig()
        assert config.timeout_seconds == 30.0

    def test_default_max_retries(self) -> None:
        config = HttpConfig()
        assert config.max_retries == 3

    def test_custom_values(self) -> None:
        config = HttpConfig(timeout_seconds=60.0, max_retries=5)
        assert config.timeout_seconds == 60.0
        assert config.max_retries == 5


class TestMediaConfig:
    """Test MediaConfig defaults and validation (Stories 3.3, 3.4)."""

    def test_default_thumbnail_size(self) -> None:
        config = MediaConfig()
        assert config.thumbnail_size == 64

    def test_default_preview_size(self) -> None:
        config = MediaConfig()
        assert config.preview_size == 512

    def test_default_max_image_dimension(self) -> None:
        config = MediaConfig()
        assert config.max_image_dimension == 4096

    def test_default_retention_days(self) -> None:
        config = MediaConfig()
        assert config.retention_original_days == 90


class TestEmbeddingConfig:
    """Test EmbeddingConfig defaults and validation (Stories 4.1, 4.3)."""

    def test_default_model(self) -> None:
        config = EmbeddingConfig()
        assert config.model == "BAAI/bge-small-en-v1.5"

    def test_default_batch_size(self) -> None:
        config = EmbeddingConfig()
        assert config.batch_size == 128

    def test_default_backend_is_local(self) -> None:
        config = EmbeddingConfig()
        assert config.backend == "local"

    def test_default_api_fields_empty(self) -> None:
        config = EmbeddingConfig()
        assert config.api_base == ""
        assert config.api_key == ""

    def test_custom_api_config(self) -> None:
        config = EmbeddingConfig(
            backend="openai",
            api_base="https://api.openai.com/v1",
            api_key="sk-test",
        )
        assert config.backend == "openai"
        assert config.api_base == "https://api.openai.com/v1"
        assert config.api_key == "sk-test"


class TestDecodeConfig:
    """Test DecodeConfig defaults and validation (Story 3.8)."""

    def test_default_quality_is_full(self) -> None:
        config = DecodeConfig()
        assert config.quality == "full"

    def test_custom_quality(self) -> None:
        config = DecodeConfig(quality="thumbnail")
        assert config.quality == "thumbnail"

    def test_all_quality_levels(self) -> None:
        for q in ("thumbnail", "preview", "full"):
            config = DecodeConfig(quality=q)
            assert config.quality == q


class TestNewConfigsInArrowLake:
    """Test that ArrowLakeConfig includes new Sprint 3 config sections."""

    def test_has_http_config(self) -> None:
        config = ArrowLakeConfig()
        assert isinstance(config.http, HttpConfig)

    def test_has_media_config(self) -> None:
        config = ArrowLakeConfig()
        assert isinstance(config.media, MediaConfig)

    def test_has_embedding_config(self) -> None:
        config = ArrowLakeConfig()
        assert isinstance(config.embedding, EmbeddingConfig)

    def test_has_decode_config(self) -> None:
        config = ArrowLakeConfig()
        assert isinstance(config.decode, DecodeConfig)

    def test_env_override_http_timeout(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("ARROW_LAKE__HTTP__TIMEOUT_SECONDS", "60.0")
        config = ArrowLakeConfig()
        assert config.http.timeout_seconds == 60.0

    def test_env_override_embedding_model(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("ARROW_LAKE__EMBEDDING__MODEL", "BAAI/bge-large-en-v1.5")
        config = ArrowLakeConfig()
        assert config.embedding.model == "BAAI/bge-large-en-v1.5"

    def test_yaml_override_new_sections(self, tmp_path: Any) -> None:
        yaml_content = """
embedding:
  model: BAAI/bge-large-en-v1.5
  batch_size: 64

media:
  thumbnail_size: 128

http:
  timeout_seconds: 10.0

decode:
  quality: thumbnail
"""
        yaml_file = tmp_path / "config.yaml"
        yaml_file.write_text(yaml_content)

        config = ArrowLakeConfig.from_yaml(str(yaml_file))
        assert config.embedding.model == "BAAI/bge-large-en-v1.5"
        assert config.embedding.batch_size == 64
        assert config.media.thumbnail_size == 128
        assert config.http.timeout_seconds == 10.0
        assert config.decode.quality == "thumbnail"
