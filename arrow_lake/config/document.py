"""Document processing configuration — PDF parsing, OCR, chunking."""

from __future__ import annotations

import logging
import warnings
from typing import Any

from pydantic import BaseModel, field_validator, model_validator

from arrow_lake.config._enums import ChunkStrategy, OcrBackend, PdfParseMode

logger = logging.getLogger(__name__)


class DocumentConfig(BaseModel):
    """Document processing configuration (v1.2).

    Attributes:
        pdf_parse_mode: PDF parsing mode — "text", "ocr", or "auto".
        ocr_backend: Primary OCR backend — "kreuzberg" or "turbo_ocr".
        ocr_fallback_enabled: Whether to fall back to secondary OCR on failure.
        ocr_endpoint: HTTP endpoint for TurboOCR service.
        ocr_timeout_seconds: Timeout for OCR processing per page.
        marker_cli_path: [DEPRECATED] Retained for backward compatibility only.
        kreuzberg_ocr_backend: Kreuzberg OCR engine (tesseract/easyocr/paddleocr).
        kreuzberg_language: OCR language codes (e.g. "eng", "eng+chi_sim").
        kreuzberg_force_ocr: Force OCR even when embedded text is extractable.
        chunk_strategy: Document chunking strategy.
        chunk_size: Target chunk size in characters (or tokens for semchunk/chonkie).
        chunk_overlap: Overlap between consecutive chunks.
        chunk_tokenizer: Tokenizer for semchunk (e.g. "cl100k_base"). Empty = char-based.
        semantic_embedding_model: HuggingFace model for chonkie semantic/sdpm chunkers.
        semantic_similarity_threshold: Similarity threshold for semantic splitting [0, 1].
        semantic_min_chunk_size: Minimum chunk size for SDPM merge phase.
        max_pages: Maximum pages to process per document (0 = unlimited).
        max_file_size_mb: Maximum file size in MB.
        store_raw_pdf: Whether to store raw PDF files in blob storage.
        blob_prefix: Blob storage prefix for raw documents.
    """

    pdf_parse_mode: PdfParseMode = PdfParseMode.AUTO
    ocr_backend: OcrBackend = OcrBackend.KREUZBERG
    ocr_fallback_enabled: bool = True
    ocr_endpoint: str = "http://localhost:8002"
    ocr_timeout_seconds: int = 300
    marker_cli_path: str = "marker_single"
    kreuzberg_ocr_backend: str = "paddleocr"
    kreuzberg_language: str = "eng"
    kreuzberg_force_ocr: bool = False
    chunk_strategy: ChunkStrategy = ChunkStrategy.RECURSIVE
    chunk_size: int = 512
    chunk_overlap: int = 64
    chunk_tokenizer: str = ""
    semantic_embedding_model: str = ""
    semantic_similarity_threshold: float = 0.5
    semantic_min_chunk_size: int = 100
    max_pages: int = 0
    max_file_size_mb: int = 100
    store_raw_pdf: bool = True
    blob_prefix: str = "documents/"

    @field_validator("ocr_backend", mode="before")
    @classmethod
    def _migrate_deprecated_ocr_backend(cls, v: Any) -> Any:
        deprecated_map = {"marker_pdf": "kreuzberg", "pypdf": "kreuzberg"}
        if isinstance(v, str) and v in deprecated_map:
            logger.warning("ocr_backend '%s' is deprecated, using '%s'", v, deprecated_map[v])
            return deprecated_map[v]
        return v

    @model_validator(mode="after")
    def _check_deprecated_fields(self) -> DocumentConfig:
        if self.marker_cli_path != "marker_single":
            warnings.warn(
                "marker_cli_path is deprecated — Kreuzberg is now the default parser",
                DeprecationWarning,
                stacklevel=2,
            )
        return self

    @field_validator("ocr_timeout_seconds")
    @classmethod
    def validate_ocr_timeout(cls, v: int) -> int:
        if v < 1:
            raise ValueError(f"ocr_timeout_seconds must be >= 1, got {v}")
        return v

    @field_validator("chunk_size")
    @classmethod
    def validate_chunk_size(cls, v: int) -> int:
        if v < 64:
            raise ValueError(f"chunk_size must be >= 64, got {v}")
        return v

    @field_validator("chunk_overlap")
    @classmethod
    def validate_chunk_overlap(cls, v: int) -> int:
        if v < 0:
            raise ValueError(f"chunk_overlap must be >= 0, got {v}")
        return v

    @field_validator("semantic_similarity_threshold")
    @classmethod
    def validate_semantic_threshold(cls, v: float) -> float:
        if not 0.0 <= v <= 1.0:
            raise ValueError(f"semantic_similarity_threshold must be in [0.0, 1.0], got {v}")
        return v
