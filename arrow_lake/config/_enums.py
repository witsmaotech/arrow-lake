"""Configuration enumerations — all StrEnum classes used across config sub-modules."""

from __future__ import annotations

from enum import StrEnum


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
    DAFT = "daft"


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
    HNSW = "HNSW"


class SchemaValidationMode(StrEnum):
    """Schema validation strictness levels."""

    STRICT = "strict"
    LENIENT = "lenient"


class FilterMode(StrEnum):
    """Quality filter combination semantics."""

    ALL = "all"
    ANY = "any"


class LLMProviderType(StrEnum):
    """Supported LLM providers for RAG generation."""

    OPENAI = "openai"
    ANTHROPIC = "anthropic"
    VLLM = "vllm"
    OLLAMA = "ollama"
    DEEPSEEK = "deepseek"
    ZHIPU = "zhipu"


class AuthMode(StrEnum):
    """认证模式枚举."""

    API_KEY = "api_key"
    JWT = "jwt"
    BOTH = "both"


class OcrBackend(StrEnum):
    """OCR engine backends for document processing."""

    KREUZBERG = "kreuzberg"
    TURBO_OCR = "turbo_ocr"


class PdfParseMode(StrEnum):
    """PDF parsing modes."""

    TEXT = "text"
    OCR = "ocr"
    AUTO = "auto"


class ChunkStrategy(StrEnum):
    """Document chunking strategies."""

    PAGE = "page"
    PARAGRAPH = "paragraph"
    RECURSIVE = "recursive"
    SEMCHUNK = "semchunk"
    CHONKIE_TOKEN = "chonkie_token"
    CHONKIE_SEMANTIC = "chonkie_semantic"
    CHONKIE_SDPM = "chonkie_sdpm"
