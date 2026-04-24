"""Search configuration — vector, FTS, hybrid, faceted, ensemble."""

from __future__ import annotations

from pydantic import BaseModel, field_validator

from arrow_lake.config._enums import DistanceMetric, VectorIndexType


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
        tokenizer_type: Tokenization strategy — "default" (lancedb built-in)
            or "jieba" (jieba CJK segmentation, recommended for Chinese).
        jieba_user_dict: Path to jieba custom dictionary file.
    """

    default_top_k: int = 10
    fts_column: str = "text_content"
    stem: bool = True
    remove_stop_words: bool = True
    lower_case: bool = True
    tokenizer_type: str = "jieba"
    jieba_user_dict: str | None = None

    @field_validator("default_top_k")
    @classmethod
    def validate_top_k(cls, v: int) -> int:
        if v < 1:
            raise ValueError(f"default_top_k must be >= 1, got {v}")
        return v

    @field_validator("tokenizer_type")
    @classmethod
    def validate_tokenizer_type(cls, v: str) -> str:
        if v not in ("default", "jieba"):
            raise ValueError(f"tokenizer_type must be 'default' or 'jieba', got '{v}'")
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
