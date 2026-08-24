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
    IVF_HNSW_SQ = "IVF_HNSW_SQ"
    IVF_SQ = "IVF_SQ"
    IVF_RQ = "IVF_RQ"
    HNSW = "HNSW"


class SchemaValidationMode(StrEnum):
    """Schema validation strictness levels."""

    STRICT = "strict"
    LENIENT = "lenient"


class FilterMode(StrEnum):
    """Quality filter combination semantics."""

    ALL = "all"
    ANY = "any"


class QualityGateMode(StrEnum):
    """Ingestion quality gate policy (v1.10.7 WP5).

    OFF: gate not constructed (ingest fast path unchanged).
    SHADOW: gate runs, counts and dead-letters, but rows pass through —
    the observability baseline before enforce (MS5 flips the default).
    ENFORCE: rejected rows are dropped before the Lance write.
    """

    OFF = "off"
    SHADOW = "shadow"
    ENFORCE = "enforce"


class OntologyGateMode(StrEnum):
    """Ontology (SHACL) gate policy at KG build finish (v1.11.0 MS1 F1.3).

    OFF: no validation, no metrics, no snapshot-side reads — zero overhead.
    SHADOW: validate + count + attach the violation summary to the build
        task, never fail the build (default; the two-week observation
        window from the master plan before any enforce flip).
    ENFORCE: reject-level violations flip the build task to FAILED with
        the violation details (the graph insert itself is already done —
        idempotent upserts — so the failure is the operator signal).
    """

    OFF = "off"
    SHADOW = "shadow"
    ENFORCE = "enforce"


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
    DOCLING = "docling"


class DoclingOcrEngine(StrEnum):
    """Docling sidecar OCR engine selection.

    See ADR docs/docling-ocr-migration-adr.md §4 for the auto-switch matrix:
      auto      → 中文/默认 rapidocr, 多语言 easyocr, 英文 tesseract
      rapidocr  → PaddleOCR PP-OCRv4 模型 (ONNX 轻量, 中文最优; #3569 强制中文)
      easyocr   → 多语言 (显式 lang 列表可控)
      tesseract → 英文最优
      none      → 不做 OCR (文字版 PDF 走文本层, 最快)
    """

    AUTO = "auto"
    RAPIDOCR = "rapidocr"
    EASYOCR = "easyocr"
    TESSERACT = "tesseract"
    NONE = "none"


class DoclingPipelineType(StrEnum):
    """Docling PDF pipeline type (ocr_backend="docling").

    STANDARD → StandardPdfPipeline: 布局 + OCR + 表格识别（默认，文字版/常规 PDF）
    VLM      → VlmPipeline: 单个视觉语言模型端到端转换整页（GraniteDocling 258M），
               适合复杂版面/扫描件/公式。详见 ADR docs/docling-ocr-migration-adr.md §P2。
    """

    STANDARD = "standard"
    VLM = "vlm"


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
    DOCLING_HYBRID = "docling_hybrid"
