"""Document processing configuration — PDF parsing, OCR, chunking."""

from __future__ import annotations

import logging
from typing import Any

from pydantic import BaseModel, field_validator

from arrow_lake.config._enums import (
    ChunkStrategy,
    DoclingOcrEngine,
    DoclingPipelineType,
    OcrBackend,
    PdfParseMode,
)

logger = logging.getLogger(__name__)


class DocumentConfig(BaseModel):
    """Document processing configuration (v1.2).

    Attributes:
        pdf_parse_mode: PDF parsing mode — "text", "ocr", or "auto".
        ocr_backend: Primary OCR backend — "kreuzberg" or "turbo_ocr".
        ocr_fallback_enabled: Whether to fall back to secondary OCR on failure.
        ocr_endpoint: HTTP endpoint for TurboOCR service.
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
    kreuzberg_ocr_backend: str = "paddleocr"
    kreuzberg_language: str = "eng"
    kreuzberg_force_ocr: bool = False
    # Docling backend (ocr_backend="docling") — Python SDK 库内嵌，
    # 支持 PDF/Office/HTML/图片/邮件多格式 + rapidocr/easyocr/tesseract OCR 切换。
    # 详见 docs/docling-ocr-migration-adr.md
    docling_ocr_engine: DoclingOcrEngine = DoclingOcrEngine.AUTO
    docling_ocr_languages: list[str] = []
    # Docling PDF 流水线类型：standard(布局+OCR+表格) / vlm(GraniteDocling 端到端视觉模型)。
    # VLM 适合复杂版面/扫描件/公式；本地 Transformers 运行时，模型下载到 HF_HOME 卷。
    docling_pipeline_type: DoclingPipelineType = DoclingPipelineType.STANDARD
    # VLM preset 名（VlmConvertOptions.from_preset）；默认 granite_docling = 258M DocTags 模型。
    docling_vlm_preset: str = "granite_docling"
    # HybridChunker 分词器（chunk_strategy="docling_hybrid" 时用）；与嵌入模型对齐 → 默认 bge-m3。
    docling_chunk_tokenizer: str = "BAAI/bge-m3"
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
