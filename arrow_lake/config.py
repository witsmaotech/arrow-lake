"""Arrow Lake configuration — 4-layer override system.

ruff noqa: F821 (false positives from __future__ annotations + forward refs)

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


class EmbeddingBackend(StrEnum):
    """Supported embedding backends."""

    LOCAL = "local"
    OPENAI = "openai"
    RAY_SERVE = "ray_serve"


class ModelSource(StrEnum):
    """Model download source."""

    HUGGINGFACE = "huggingface"
    MODELSCOPE = "modelscope"


class DecodeQuality(StrEnum):
    """Image decode fidelity levels."""

    THUMBNAIL = "thumbnail"
    PREVIEW = "preview"
    FULL = "full"


class DistanceMetric(StrEnum):
    """Supported vector distance metrics."""

    COSINE = "cosine"
    L2 = "l2"
    DOT = "dot"


class VectorIndexType(StrEnum):
    """Supported vector index types."""

    IVF_PQ = "IVF_PQ"
    IVF_FLAT = "IVF_FLAT"
    IVF_HNSW_PQ = "IVF_HNSW_PQ"


class SchemaValidationMode(StrEnum):
    """Schema validation strictness levels."""

    STRICT = "strict"
    LENIENT = "lenient"


class FilterMode(StrEnum):
    """Quality filter combination semantics."""

    ALL = "all"
    ANY = "any"


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


class MediaConfig(BaseModel):
    """Media processing configuration (Stories 3.3, 3.4).

    Attributes:
        thumbnail_size: Thumbnail image dimension (square).
        preview_size: Preview image dimension (square).
        max_image_dimension: Maximum allowed image dimension before downscaling.
        retention_original_days: Days to retain original full-resolution images.
    """

    thumbnail_size: int = 64
    preview_size: int = 512
    max_image_dimension: int = 4096
    retention_original_days: int = 90


class EmbeddingConfig(BaseModel):
    """Text embedding configuration (Stories 4.1, 4.3).

    Attributes:
        model: HuggingFace model name for local embedding.
        model_source: Model download source — "huggingface" or "modelscope".
        batch_size: Number of texts to embed per batch.
        backend: Embedding backend — "local", "openai", or "ray_serve".
        api_base: Base URL for external embedding API.
        api_key: API key for external embedding API.
    """

    model: str = "Qwen/Qwen3-Embedding-0.6B"
    model_source: ModelSource = ModelSource.HUGGINGFACE
    batch_size: int = 128
    backend: EmbeddingBackend = EmbeddingBackend.LOCAL
    api_base: str = ""
    api_key: str = ""


class DecodeConfig(BaseModel):
    """Image decode fidelity configuration (Story 3.8).

    Attributes:
        quality: Default decode quality — "thumbnail", "preview", or "full".
    """

    quality: DecodeQuality = DecodeQuality.FULL


class VectorSearchConfig(BaseModel):
    """Vector similarity search configuration (Story 5.1).

    Attributes:
        metric: Default distance metric for vector search.
        default_index_type: Default vector index type.
        default_top_k: Default number of results to return.
        num_partitions: IVF partitions (auto-adjusted for large datasets).
        num_sub_vectors: PQ sub-vector count (must be multiple of 8).
        num_bits: PQ quantization bits per sub-vector.
        nprobes: Number of IVF partitions to probe during search.
        max_nprobes: Maximum nprobes for large-scale search.
    """

    metric: DistanceMetric = DistanceMetric.COSINE
    default_index_type: VectorIndexType = VectorIndexType.IVF_PQ
    default_top_k: int = 10
    num_partitions: int = 256
    num_sub_vectors: int = 24
    num_bits: int = 8
    nprobes: int = 20
    max_nprobes: int = 256

    @field_validator("default_top_k")
    @classmethod
    def validate_top_k(cls, v: int) -> int:
        if v < 1:
            raise ValueError(f"default_top_k must be >= 1, got {v}")
        return v

    @field_validator("num_sub_vectors")
    @classmethod
    def validate_num_sub_vectors(cls, v: int) -> int:
        if v < 1 or v % 8 != 0:
            raise ValueError(f"num_sub_vectors must be a positive multiple of 8, got {v}")
        return v


class FullTextSearchConfig(BaseModel):
    """Full-text search configuration (Story 5.2).

    Attributes:
        default_top_k: Default number of results to return.
        fts_column: Default text column for FTS indexing.
        stem: Whether to apply stemming during tokenization.
        remove_stop_words: Whether to remove stop words.
        lower_case: Whether to lowercase tokens.
    """

    default_top_k: int = 10
    fts_column: str = "text_content"
    stem: bool = True
    remove_stop_words: bool = True
    lower_case: bool = True

    @field_validator("default_top_k")
    @classmethod
    def validate_top_k(cls, v: int) -> int:
        if v < 1:
            raise ValueError(f"default_top_k must be >= 1, got {v}")
        return v


class HybridSearchConfig(BaseModel):
    """Hybrid search configuration (Story 5.3).

    Attributes:
        default_top_k: Default number of final results to return.
        rrf_k: RRF constant (paper recommends K=60).
        vector_top_k_multiplier: Vector candidate count = default_top_k * multiplier.
        fts_top_k_multiplier: FTS candidate count = default_top_k * multiplier.
    """

    default_top_k: int = 10
    rrf_k: int = 60
    vector_top_k_multiplier: int = 3
    fts_top_k_multiplier: int = 3

    @field_validator("default_top_k", "rrf_k", "vector_top_k_multiplier", "fts_top_k_multiplier")
    @classmethod
    def validate_positive_int(cls, v: int) -> int:
        if v < 1:
            raise ValueError(f"value must be >= 1, got {v}")
        return v


class QualityConfig(BaseModel):
    """Quality filtering and schema validation configuration (Epic 4).

    Attributes:
        enabled: Whether quality filtering is active.
        filter_mode: AND ('all') or OR ('any') filter combination.
        active_filters: Comma-separated names of enabled filters from registry.
        schema_validation: strict rejects unknown cols + type mismatches;
                          lenient drops unknown cols, safe-casts compatible types.
        dead_letter_enabled: Whether rejected rows go to dead-letter table.
        text_min_chars: Minimum text length for TextLengthFilter.
        text_max_chars: Maximum text length for TextLengthFilter.
        image_min_width: Minimum image width for ImageResolutionFilter.
        image_min_height: Minimum image height for ImageResolutionFilter.
    """

    enabled: bool = True
    filter_mode: FilterMode = FilterMode.ALL
    active_filters: str = ""
    schema_validation: SchemaValidationMode = SchemaValidationMode.LENIENT
    dead_letter_enabled: bool = True
    text_min_chars: int = 1
    text_max_chars: int | None = None
    image_min_width: int = 64
    image_min_height: int = 64

    # NeMo Curator (Sprint 9, Story 8.5)
    nemo_curator_enabled: bool = False
    nemo_curator_model: str = "nemo/quality-scorer"
    nemo_curator_threshold: float = 0.5
    nemo_curator_batch_size: int = 64

    # Content dedup (Story 4.7)
    dedup_enabled: bool = False
    dedup_strategy: str = "exact"
    dedup_action: str = "flag"
    dedup_perceptual_threshold: int = 10

    @field_validator("dedup_strategy")
    @classmethod
    def validate_dedup_strategy(cls, v: str) -> str:
        if v not in ("exact", "perceptual", "both"):
            raise ValueError(f"dedup_strategy must be 'exact', 'perceptual', or 'both', got {v!r}")
        return v

    @field_validator("dedup_action")
    @classmethod
    def validate_dedup_action(cls, v: str) -> str:
        if v not in ("flag", "remove"):
            raise ValueError(f"dedup_action must be 'flag' or 'remove', got {v!r}")
        return v


class OlapConfig(BaseModel):
    """OLAP analytics configuration (Story 5.4, 7.6).

    Attributes:
        max_result_rows: Maximum number of rows returned by OLAP queries.
        enable_predicate_pushdown: Whether to push down predicates to Lance.
        enable_join: Whether JOIN queries are allowed.
        scanner_batch_size: Rows per batch when streaming via Lance scanner.
        enable_streaming: Use RecordBatchReader streaming instead of full
            materialization for SQL queries.
    """

    max_result_rows: int = 100_000
    enable_predicate_pushdown: bool = True
    enable_join: bool = True
    scanner_batch_size: int = 10_000
    enable_streaming: bool = True

    @field_validator("max_result_rows")
    @classmethod
    def validate_max_result_rows(cls, v: int) -> int:
        if v < 1:
            raise ValueError(f"max_result_rows must be >= 1, got {v}")
        return v


class DaftConfig(BaseModel):
    """Daft DataFrame engine configuration (Story 3.7).

    Attributes:
        enabled: Whether Daft query engine is available via Lake.daft_query().
    """

    enabled: bool = True


class WorkflowConfig(BaseModel):
    """Workflow orchestration configuration (Epic 6).

    Attributes:
        max_retry_attempts: Maximum retry attempts per step.
        min_backoff_seconds: Minimum backoff between retries (exponential).
        max_backoff_seconds: Maximum backoff between retries.
        checkpoint_enabled: Enable Lance version checkpointing before steps.
        ray_execution_enabled: Enable Ray cluster execution (--with ray).
        auto_tag_runs: Auto-generate tags from run metadata.
        artifact_retention_days: Days to retain Argo workflow artifacts.
    """

    max_retry_attempts: int = 3
    min_backoff_seconds: float = 1.0
    max_backoff_seconds: float = 60.0
    checkpoint_enabled: bool = True
    ray_execution_enabled: bool = False
    auto_tag_runs: bool = True
    artifact_retention_days: int = 30
    schedule_cron: str | None = None

    @field_validator("max_retry_attempts")
    @classmethod
    def validate_max_retry_attempts(cls, v: int) -> int:
        if v < 0:
            raise ValueError(f"max_retry_attempts must be >= 0, got {v}")
        return v

    @field_validator("min_backoff_seconds", "max_backoff_seconds")
    @classmethod
    def validate_backoff(cls, v: float) -> float:
        if v < 0:
            raise ValueError(f"backoff_seconds must be >= 0, got {v}")
        return v


class ArgoConfig(BaseModel):
    """Argo Workflows configuration (Story 7.3).

    Attributes:
        namespace: Kubernetes namespace for Argo workflows.
        service_account: Service account for workflow pods.
        workflow_timeout: Workflow execution timeout in seconds.
        image: Container image for workflow pods.
        image_pull_policy: Image pull policy.
        artifact_storage: Storage backend for Argo artifacts (s3:// or minio://).
    """

    namespace: str = "default"
    service_account: str = "arrow-lake"
    workflow_timeout: int = 3600
    image: str = "arrow-lake:latest"
    image_pull_policy: str = "IfNotPresent"
    artifact_storage: str = ""

    @field_validator("workflow_timeout")
    @classmethod
    def validate_timeout(cls, v: int) -> int:
        if v < 60:
            raise ValueError(f"workflow_timeout must be >= 60 seconds, got {v}")
        return v


class AutoscaleConfig(BaseModel):
    """GPU autoscaling configuration (Story 7.5).

    Attributes:
        enabled: Whether GPU autoscaling is active.
        min_workers: Minimum GPU workers (0 = scale to zero).
        max_workers: Maximum GPU workers.
        scale_up_timeout_seconds: Max wait time for scale-up.
        idle_timeout_seconds: Seconds of inactivity before scale-down.
        spot_preference: Prefer spot instances (0.0=on-demand, 1.0=spot-only).
        gpu_increment: Fractional GPU increment (0.5 = half-GPU steps).
    """

    enabled: bool = False
    min_workers: int = 0
    max_workers: int = 8
    scale_up_timeout_seconds: int = 300
    idle_timeout_seconds: int = 600
    spot_preference: float = 0.8
    gpu_increment: float = 0.5

    @field_validator("max_workers")
    @classmethod
    def validate_max_workers(cls, v: int) -> int:
        if v < 1:
            raise ValueError(f"max_workers must be >= 1, got {v}")
        return v

    @field_validator("scale_up_timeout_seconds", "idle_timeout_seconds")
    @classmethod
    def validate_timeout(cls, v: int) -> int:
        if v < 60:
            raise ValueError(f"timeout must be >= 60 seconds, got {v}")
        return v

    @field_validator("spot_preference")
    @classmethod
    def validate_spot_preference(cls, v: float) -> float:
        if not 0.0 <= v <= 1.0:
            raise ValueError(f"spot_preference must be 0.0-1.0, got {v}")
        return v

    @field_validator("gpu_increment")
    @classmethod
    def validate_gpu_increment(cls, v: float) -> float:
        if v not in (0.5, 1.0):
            raise ValueError(f"gpu_increment must be 0.5 or 1.0, got {v}")
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


class FacetedSearchConfig(BaseModel):
    """Faceted search configuration (Story 8.1).

    Attributes:
        max_facet_values: Maximum number of facet values to return per facet.
        default_facet_columns: Default columns to compute facets for.
        facet_filter_columns: Columns allowed for faceted filtering.
    """

    max_facet_values: int = 50
    default_facet_columns: list[str] = ["modality", "source"]
    facet_filter_columns: list[str] = [
        "modality",
        "source",
        "quality_score",
        "created_at",
    ]

    @field_validator("max_facet_values")
    @classmethod
    def validate_max_facet_values(cls, v: int) -> int:
        if v < 1:
            raise ValueError(f"max_facet_values must be >= 1, got {v}")
        return v


class EnsembleSearchConfig(BaseModel):
    """Ensemble search configuration (Sprint 9, Story 8.2).

    Attributes:
        default_top_k: Default number of results.
        rrf_k: RRF smoothing constant.
        fusion_method: Fusion method (only "rrf" supported).
        candidate_multiplier: Per-column candidate pool size.
    """

    default_top_k: int = 10
    rrf_k: int = 60
    fusion_method: str = "rrf"
    candidate_multiplier: int = 3

    @field_validator("default_top_k", "rrf_k", "candidate_multiplier")
    @classmethod
    def validate_positive_int(cls, v: int) -> int:
        if v < 1:
            raise ValueError(f"value must be >= 1, got {v}")
        return v


class ExportConfig(BaseModel):
    """Data export configuration (Story 5.9).

    Attributes:
        default_format: Default export format ("parquet" or "csv").
        parquet_compression: Compression codec for Parquet files.
        csv_delimiter: Delimiter for CSV files.
        allow_overwrite: Whether overwriting existing files is allowed.
    """

    default_format: str = "parquet"
    parquet_compression: str = "snappy"
    csv_delimiter: str = ","
    allow_overwrite: bool = False

    @field_validator("default_format")
    @classmethod
    def validate_format(cls, v: str) -> str:
        if v not in ("parquet", "csv"):
            raise ValueError(f"format must be 'parquet' or 'csv', got {v!r}")
        return v

    @field_validator("parquet_compression")
    @classmethod
    def validate_compression(cls, v: str) -> str:
        valid = {"snappy", "gzip", "brotli", "zstd", "lz4", "none"}
        if v not in valid:
            raise ValueError(f"parquet_compression must be one of {valid}, got {v!r}")
        return v


class LineageConfig(BaseModel):
    """Data lineage configuration (Sprint 9, Story 8.3).

    Attributes:
        enabled: Whether lineage tracking is active.
        store_dataset: Name of the lineage events dataset.
        auto_record: Automatically record lineage on dataset operations.
    """

    enabled: bool = False
    store_dataset: str = "_lineage_events"
    auto_record: bool = True


class AuditConfig(BaseModel):
    """Event sourcing audit configuration (Sprint 9, Story 8.4).

    Attributes:
        enabled: Whether audit trail is active.
        hmac_secret_key: Secret key for HMAC. Empty disables HMAC.
        audit_dataset: Name of the audit trail dataset.
        auto_record_workflow: Auto-record workflow events.
    """

    enabled: bool = False
    hmac_secret_key: str = ""
    audit_dataset: str = "_audit_trail"
    auto_record_workflow: bool = True


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
    http: HttpConfig = HttpConfig()
    media: MediaConfig = MediaConfig()
    embedding: EmbeddingConfig = EmbeddingConfig()
    decode: DecodeConfig = DecodeConfig()
    vector: VectorSearchConfig = VectorSearchConfig()
    fts: FullTextSearchConfig = FullTextSearchConfig()
    hybrid: HybridSearchConfig = HybridSearchConfig()
    olap: OlapConfig = OlapConfig()
    daft: DaftConfig = DaftConfig()
    quality: QualityConfig = QualityConfig()
    workflow: WorkflowConfig = WorkflowConfig()
    argo: ArgoConfig = ArgoConfig()
    autoscale: AutoscaleConfig = AutoscaleConfig()
    lifecycle: LifecycleConfig = LifecycleConfig()
    faceted: FacetedSearchConfig = FacetedSearchConfig()
    ensemble: EnsembleSearchConfig = EnsembleSearchConfig()
    lineage: LineageConfig = LineageConfig()
    audit: AuditConfig = AuditConfig()
    export: ExportConfig = ExportConfig()

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
            http=merged["http"],
            media=merged["media"],
            embedding=merged["embedding"],
            decode=merged["decode"],
            vector=merged["vector"],
            fts=merged["fts"],
            hybrid=merged["hybrid"],
            olap=merged["olap"],
            daft=merged["daft"],
            quality=merged["quality"],
            workflow=merged["workflow"],
            argo=merged["argo"],
            autoscale=merged["autoscale"],
            lifecycle=merged["lifecycle"],
            faceted=merged["faceted"],
            ensemble=merged["ensemble"],
            lineage=merged["lineage"],
            audit=merged["audit"],
            export=merged["export"],
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
        "http": HttpConfig,
        "media": MediaConfig,
        "embedding": EmbeddingConfig,
        "decode": DecodeConfig,
        "vector": VectorSearchConfig,
        "fts": FullTextSearchConfig,
        "hybrid": HybridSearchConfig,
        "olap": OlapConfig,
        "daft": DaftConfig,
        "quality": QualityConfig,
        "workflow": WorkflowConfig,
        "argo": ArgoConfig,
        "autoscale": AutoscaleConfig,
        "lifecycle": LifecycleConfig,
        "faceted": FacetedSearchConfig,
        "ensemble": EnsembleSearchConfig,
        "lineage": LineageConfig,
        "export": ExportConfig,
        "audit": AuditConfig,
    }
    result: dict[str, Any] = {}

    for section, model_cls in section_types.items():
        base_dict = getattr(base, section).model_dump()
        if section in yaml_data:
            # Merge YAML keys into base, YAML wins on conflict
            base_dict.update(yaml_data[section])
        result[section] = model_cls(**base_dict)

    return result
