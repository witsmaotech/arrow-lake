"""Arrow Lake configuration — 4-layer override system.

Priority (low → high):
  1. Code defaults (Pydantic field defaults)
  2. .env file (via pydantic-settings)
  3. Environment variables (ARROW_LAKE__ prefix, via pydantic-settings)
  4. YAML config file (explicit load, highest priority)

See project-context.md Rules 3, 35-38.
"""

from __future__ import annotations

from enum import StrEnum
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class StorageBackend(StrEnum):
    """Supported storage backends."""

    MINIO = "minio"
    S3 = "s3"
    GCS = "gcs"
    LOCAL = "local"


class LogLevel(StrEnum):
    """Valid log levels (matches Python logging + structlog)."""

    DEBUG = "DEBUG"
    INFO = "INFO"
    WARNING = "WARNING"
    ERROR = "ERROR"
    CRITICAL = "CRITICAL"


class StorageConfig(BaseModel):
    """Storage layer configuration.

    Attributes:
        backend: Storage backend type (minio, s3, gcs, local).
        s3_endpoint: S3-compatible endpoint URL.
        s3_access_key: S3 access key (empty = use default credentials).
        s3_secret_key: S3 secret key (empty = use default credentials).
        s3_bucket: Default bucket name.
        s3_region: S3 region.
    """

    backend: StorageBackend = StorageBackend.MINIO
    s3_endpoint: str = "http://localhost:9000"
    s3_access_key: str = ""
    s3_secret_key: str = ""
    s3_bucket: str = "arrow-lake"
    s3_region: str = "us-east-1"


class ComputeConfig(BaseModel):
    """Compute layer configuration.

    Attributes:
        gpu_enabled: Whether GPU acceleration is available.
        ray_address: Ray cluster address ('auto' for local cluster).
        num_workers: Number of Ray worker processes.
    """

    gpu_enabled: bool = False
    ray_address: str = "auto"
    num_workers: int = 2

    @field_validator("num_workers")
    @classmethod
    def validate_num_workers(cls, v: int) -> int:
        if v < 1:
            raise ValueError(f"num_workers must be >= 1, got {v}")
        return v


class ObservabilityConfig(BaseModel):
    """Observability configuration.

    Attributes:
        metrics_enabled: Whether Prometheus metrics endpoint is enabled.
        metrics_port: Port for the metrics HTTP server.
        log_level: Logging verbosity level.
        correlation_id: Trace correlation ID (auto-generated UUID if empty).
    """

    metrics_enabled: bool = True
    metrics_port: int = 8000
    log_level: LogLevel = LogLevel.INFO
    correlation_id: str = ""

    @field_validator("metrics_port")
    @classmethod
    def validate_metrics_port(cls, v: int) -> int:
        if not 1 <= v <= 65535:
            raise ValueError(f"metrics_port must be between 1 and 65535, got {v}")
        return v


class ArrowLakeConfig(BaseSettings):
    """Top-level Arrow Lake configuration.

    Loads from env vars with ARROW_LAKE__ prefix via pydantic-settings.
    Use ``from_yaml()`` for highest-priority YAML config overlay.

    Example::

        config = ArrowLakeConfig()  # loads defaults + .env + env
        config = ArrowLakeConfig.from_yaml("configs/prod.yaml")
    """

    model_config = SettingsConfigDict(
        env_prefix="ARROW_LAKE__",
        env_nested_delimiter="__",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    storage: StorageConfig = StorageConfig()
    compute: ComputeConfig = ComputeConfig()
    observability: ObservabilityConfig = ObservabilityConfig()

    @classmethod
    def from_yaml(cls, path: str | Path) -> ArrowLakeConfig:
        """Load config with YAML overlay (highest priority layer).

        Constructs the base config first (defaults + .env + env vars),
        then overlays YAML values on top.

        Args:
            path: Path to a YAML config file.

        Returns:
            ArrowLakeConfig with YAML values merged over defaults + .env + env.

        Raises:
            FileNotFoundError: If the YAML file does not exist.
        """
        file_path = Path(path)
        if not file_path.exists():
            raise FileNotFoundError(f"Config file not found: {file_path}")

        with open(file_path) as f:
            raw: dict[str, Any] = yaml.safe_load(f) or {}

        # Build base config (layers 1-3: defaults + .env + env vars)
        base = cls()

        # Merge YAML (layer 4) onto base: for each section, deep-merge
        # YAML values into the existing base section so env overrides
        # on unoverridden keys are preserved.
        merged = _build_merged_update(base, raw)
        # Use direct construction with validated sub-models to ensure
        # validators (num_workers, metrics_port) run on YAML values.
        return cls(
            storage=merged["storage"],
            compute=merged["compute"],
            observability=merged["observability"],
        )


def _build_merged_update(base: ArrowLakeConfig, yaml_data: dict[str, Any]) -> dict[str, Any]:
    """Deep-merge YAML values onto base config sections.

    For each section present in the YAML, merge its keys into
    the corresponding base section. Sections absent from YAML
    keep their base (env-inherited) values entirely.
    """
    section_types: dict[str, type[BaseModel]] = {
        "storage": StorageConfig,
        "compute": ComputeConfig,
        "observability": ObservabilityConfig,
    }
    result: dict[str, Any] = {}

    for section, model_cls in section_types.items():
        base_dict = getattr(base, section).model_dump()
        if section in yaml_data:
            # Merge YAML keys into base, YAML wins on conflict
            base_dict.update(yaml_data[section])
        result[section] = model_cls(**base_dict)

    return result
