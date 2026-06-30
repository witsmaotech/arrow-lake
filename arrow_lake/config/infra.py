"""Infrastructure configuration — compute, observability, HTTP, Daft, lifecycle."""

from __future__ import annotations

from pydantic import BaseModel, field_validator

from arrow_lake.config._enums import LogLevel


class ComputeConfig(BaseModel):
    """Compute layer configuration.

    Attributes:
        gpu_enabled: Whether GPU acceleration is available.
        ray_address: Ray cluster address ('auto' for local cluster).
        ray_dashboard_url: Ray dashboard base URL for health probing
            (e.g. ``http://ray-head:8265``). Empty ⇒ Ray probe skipped.
        num_workers: Number of Ray worker processes.
    """

    gpu_enabled: bool = False
    ray_address: str = "auto"
    ray_dashboard_url: str = ""
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
    metrics_port: int = 8001  # Default differs from API port (8000) to avoid conflict
    metrics_path: str = "/metrics"
    log_level: LogLevel = LogLevel.INFO
    correlation_id: str = ""

    @field_validator("metrics_port")
    @classmethod
    def validate_metrics_port(cls, v: int) -> int:
        if not 1 <= v <= 65535:
            raise ValueError(f"metrics_port must be between 1 and 65535, got {v}")
        return v


class HttpConfig(BaseModel):
    """HTTP client configuration (Story 3.2).

    Attributes:
        timeout_seconds: Request timeout in seconds.
        max_retries: Maximum retry attempts for transient failures.
    """

    timeout_seconds: float = 30.0
    max_retries: int = 3

    @field_validator("timeout_seconds")
    @classmethod
    def validate_timeout(cls, v: float) -> float:
        if v < 1.0:
            raise ValueError(f"timeout_seconds must be >= 1.0, got {v}")
        return v


class ResourceLimits(BaseModel):
    """Resource limits for query execution (v1.6.0 Phase 2)."""

    max_query_time_seconds: int = 300
    max_concurrent_queries: int = 10
    max_result_rows: int = 1_000_000
    max_scan_bytes: int = 10_000_000_000


class BackpressureConfig(BaseModel):
    """Ingest backpressure configuration (v1.6.0 Phase 2)."""

    ingest_queue_size: int = 10_000
    rejection_threshold: float = 0.9
    retry_max_attempts: int = 3


class DaftConfig(BaseModel):
    """Daft DataFrame engine configuration (Story 3.7).

    Attributes:
        enabled: Whether Daft query engine is available via Lake.daft_query().
        default_num_partitions: Default number of partitions for Daft operations.
        target_partition_max_memory_bytes: Memory cap per partition in bytes.
        read_num_threads: Number of threads for parallel file reads.
        ingest_use_daft_pipeline: Use Daft DataFrame pipeline for ingestion transforms.
    """

    enabled: bool = True
    default_num_partitions: int = 10
    target_partition_max_memory_bytes: int = 256 * 1024 * 1024
    read_num_threads: int = 4
    ingest_use_daft_pipeline: bool = True

    @field_validator("default_num_partitions", "read_num_threads")
    @classmethod
    def validate_positive(cls, v: int) -> int:
        if v < 1:
            raise ValueError(f"must be >= 1, got {v}")
        return v

    @field_validator("target_partition_max_memory_bytes")
    @classmethod
    def validate_memory(cls, v: int) -> int:
        if v < 16 * 1024 * 1024:
            raise ValueError(f"must be >= 16MB, got {v}")
        return v


class LifecycleConfig(BaseModel):
    """Blob lifecycle configuration (Story 7.7).

    Attributes:
        enabled: Whether automatic lifecycle tiering is active.
        standard_to_ia_days: Days in Standard before transition to IA.
        ia_to_glacier_days: Days in IA before transition to Glacier.
        glacier_expiration_days: Days in Glacier before expiration/deletion.
        excluded_prefixes: S3 key prefixes excluded from lifecycle transitions.
        glacier_retrieval_tier: Glacier retrieval tier (Expedited/Standard/Bulk).
    """

    enabled: bool = False
    standard_to_ia_days: int = 30
    ia_to_glacier_days: int = 90
    glacier_expiration_days: int = 365
    excluded_prefixes: list[str] = ["thumbnails/", "previews/"]
    glacier_retrieval_tier: str = "Standard"

    @field_validator("standard_to_ia_days", "ia_to_glacier_days", "glacier_expiration_days")
    @classmethod
    def validate_days(cls, v: int) -> int:
        if v < 1:
            raise ValueError(f"days must be >= 1, got {v}")
        return v

    @field_validator("glacier_retrieval_tier")
    @classmethod
    def validate_retrieval_tier(cls, v: str) -> str:
        valid = {"Expedited", "Standard", "Bulk"}
        if v not in valid:
            raise ValueError(f"glacier_retrieval_tier must be one of {valid}, got {v!r}")
        return v
