"""Media processing configuration — media, embedding, decode, quality, export."""

from __future__ import annotations

from pydantic import BaseModel, field_validator

from arrow_lake.config._enums import (
    DecodeQuality,
    EmbeddingBackend,
    FilterMode,
    ModelSource,
    SchemaValidationMode,
)

QWEN3_VL_EMBEDDING_MODELS: dict[str, dict[str, object]] = {
    "Qwen/Qwen3-VL-Embedding-2B": {"dim": 2048, "multimodal": True},
    "Qwen/Qwen3-VL-Embedding-8B": {"dim": 4096, "multimodal": True},
}


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
    """Text embedding configuration (Stories 4.1, 4.3, v1.2).

    Attributes:
        model: HuggingFace model name for local embedding.
        model_source: Model download source — "huggingface" or "modelscope".
        batch_size: Number of texts to embed per batch.
        backend: Embedding backend — "local", "openai", or "ray_serve".
        api_base: Base URL for external embedding API.
        api_key: API key for external embedding API.
        expected_dim: Expected embedding dimension (0 = auto-detect from model).
        validate_dimension: Whether to validate embedding dimension after model load.
    """

    model: str = "Qwen/Qwen3-Embedding-0.6B"
    model_source: ModelSource = ModelSource.HUGGINGFACE
    batch_size: int = 128
    backend: EmbeddingBackend = EmbeddingBackend.LOCAL
    api_base: str = ""
    api_key: str = ""
    expected_dim: int = 0
    validate_dimension: bool = True

    @property
    def known_dimension(self) -> int:
        """Return known dimension for whitelisted models, or 0 if unknown."""
        info = QWEN3_VL_EMBEDDING_MODELS.get(self.model)
        if info:
            return info["dim"]
        return 0

    @property
    def is_multimodal(self) -> bool:
        """Check if the configured model supports multimodal input."""
        info = QWEN3_VL_EMBEDDING_MODELS.get(self.model)
        if info:
            return bool(info.get("multimodal"))
        return False


class DecodeConfig(BaseModel):
    """Image decode fidelity configuration (Story 3.8).

    Attributes:
        quality: Default decode quality — "thumbnail", "preview", or "full".
    """

    quality: DecodeQuality = DecodeQuality.FULL


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
    base_dir: str = "/app/exports"

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
