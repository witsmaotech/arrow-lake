"""Document parsing pipeline — PDF extraction via Kreuzberg / TurboOCR.

Provides DocumentParser for extracting text from documents using Kreuzberg
(Rust-core, Python bindings, 91+ formats, built-in OCR) as the primary engine,
with optional TurboOCR GPU fallback.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

from arrow_lake.config._enums import OcrBackend, PdfParseMode
from arrow_lake.config.document import DocumentConfig
from arrow_lake.exceptions import DocumentError, ErrorCode

try:
    from kreuzberg import ExtractionConfig, OcrConfig, PageConfig, extract_file_sync

    _KREUZBERG_AVAILABLE = True
except ImportError:
    _KREUZBERG_AVAILABLE = False
    ExtractionConfig = None  # type: ignore[assignment, misc]
    OcrConfig = None  # type: ignore[assignment, misc]
    PageConfig = None  # type: ignore[assignment, misc]
    extract_file_sync = None  # type: ignore[assignment]

if TYPE_CHECKING:
    from kreuzberg import ExtractionResult

logger = logging.getLogger(__name__)

__all__ = ["DocumentParser", "ParsedDocument"]


@dataclass(frozen=True)
class ParsedDocument:
    """Result of document parsing.

    Attributes:
        text: Full extracted text.
        pages: List of (page_number, page_text) tuples.
        page_count: Total pages.
        backend: Which parser was used.
        blob_key: S3/MinIO key where raw file was stored (empty if not stored).
    """

    text: str
    pages: tuple[tuple[int, str], ...]
    page_count: int
    backend: str
    blob_key: str = ""


def _build_extraction_config(cfg: DocumentConfig, mode: PdfParseMode):
    """Build Kreuzberg ExtractionConfig from DocumentConfig and PdfParseMode."""
    if not _KREUZBERG_AVAILABLE:
        raise DocumentError(
            error_code=ErrorCode.DOCUMENT_PARSE_FAILED,
            message="kreuzberg package is not installed. Install with: pip install arrow-lake[document]",
        )

    force_ocr = (mode == PdfParseMode.OCR) or cfg.kreuzberg_force_ocr
    ocr = None
    if force_ocr or mode == PdfParseMode.AUTO:
        ocr = OcrConfig(
            backend=cfg.kreuzberg_ocr_backend,
            language=cfg.kreuzberg_language,
        )
    return ExtractionConfig(
        ocr=ocr,
        force_ocr=force_ocr,
        pages=PageConfig(extract_pages=True),
    )


def _result_to_pages(
    result: Any, max_pages: int,
) -> tuple[tuple[int, str], ...]:
    """Convert Kreuzberg ExtractionResult to ParsedDocument pages format.

    Kreuzberg returns pages as list of dicts: {"page_number": int, "content": str, ...}.
    """
    pages: list[tuple[int, str]] = []
    for page in result.pages or []:
        if isinstance(page, dict):
            page_num = page.get("page_number", len(pages) + 1)
            text = (page.get("content") or "").strip()
        elif isinstance(page, str):
            page_num = len(pages) + 1
            text = page.strip()
        else:
            continue
        if text:
            pages.append((page_num, text))
        if max_pages > 0 and len(pages) >= max_pages:
            break
    return tuple(pages)


class DocumentParser:
    """Document parser using Kreuzberg with optional TurboOCR fallback.

    Parse order depends on ocr_backend and pdf_parse_mode:
    - ocr_backend="kreuzberg": Kreuzberg handles all parsing (default)
    - ocr_backend="turbo_ocr": TurboOcrClient primary, Kreuzberg fallback

    PdfParseMode controls OCR behavior:
    - "text": Text extraction only (no OCR)
    - "ocr": Force OCR on all pages
    - "auto": Try text first, OCR if content is sparse

    Args:
        config: Document processing configuration.
    """

    def __init__(self, config: DocumentConfig | None = None) -> None:
        self._config = config or DocumentConfig()

    def parse(
        self,
        file_path: str | Path,
        *,
        ocr_client: Any = None,
        max_pages: int = 0,
    ) -> ParsedDocument:
        """Parse a document file.

        Args:
            file_path: Path to the document file.
            ocr_client: Optional TurboOcrClient for GPU OCR fallback.
            max_pages: Maximum pages to return (0 = all).

        Returns:
            ParsedDocument with extracted text and metadata.

        Raises:
            DocumentError: If parsing fails on all backends.
            FileNotFoundError: If the file does not exist.
        """
        file_path = Path(file_path)
        if not file_path.exists():
            raise FileNotFoundError(f"File not found: {file_path}")

        effective_max = max_pages or self._config.max_pages

        if self._config.ocr_backend == OcrBackend.TURBO_OCR:
            return self._parse_turbo_ocr_primary(file_path, ocr_client, effective_max)

        return self._parse_kreuzberg(file_path, effective_max)

    def _parse_kreuzberg(
        self, file_path: Path, max_pages: int,
    ) -> ParsedDocument:
        """Parse using Kreuzberg (primary path)."""
        if not _KREUZBERG_AVAILABLE:
            raise DocumentError(
                error_code=ErrorCode.DOCUMENT_PARSE_FAILED,
                message="kreuzberg package is not installed. Install with: pip install arrow-lake[document]",
            )

        mode = self._config.pdf_parse_mode
        cfg = _build_extraction_config(self._config, mode)

        try:
            result = extract_file_sync(str(file_path), config=cfg)  # type: ignore[misc]
        except (OSError, ValueError, RuntimeError) as exc:
            raise DocumentError(
                error_code=ErrorCode.DOCUMENT_PARSE_FAILED,
                message=f"Kreuzberg failed to parse '{file_path}': {exc}",
            ) from exc

        pages = _result_to_pages(result, max_pages)

        if not pages:
            raise DocumentError(
                error_code=ErrorCode.DOCUMENT_PARSE_FAILED,
                message=f"No text extracted from '{file_path}' — file may be empty or corrupted",
            )

        full_text = "\n\n".join(text for _, text in pages)

        return ParsedDocument(
            text=full_text,
            pages=pages,
            page_count=len(pages),
            backend="kreuzberg",
        )

    def _parse_turbo_ocr_primary(
        self, file_path: Path, ocr_client: Any, max_pages: int,
    ) -> ParsedDocument:
        """Parse using TurboOcrClient as primary, Kreuzberg as fallback."""
        if ocr_client is not None and getattr(ocr_client, "is_available", lambda: False)():
            try:
                return self._ocr_via_client(file_path, ocr_client, max_pages)
            except DocumentError:
                logger.warning("turbo_ocr_failed_trying_kreuzberg path=%s", file_path)

        if self._config.ocr_fallback_enabled:
            logger.info("falling_back_to_kreuzberg path=%s", file_path)
            return self._parse_kreuzberg(file_path, max_pages)

        raise DocumentError(
            error_code=ErrorCode.DOCUMENT_PARSE_FAILED,
            message=f"All parsing backends failed for '{file_path}'",
        )

    def _ocr_via_client(
        self, pdf_path: Path, ocr_client: Any, max_pages: int,
    ) -> ParsedDocument:
        """OCR via TurboOcrClient and split into pages."""
        pdf_bytes = pdf_path.read_bytes()
        try:
            result = ocr_client.ocr(pdf_bytes, filename=pdf_path.name)
        except (ConnectionError, TimeoutError, OSError, ValueError, RuntimeError) as exc:
            raise DocumentError(
                error_code=ErrorCode.DOCUMENT_OCR_FAILED,
                message=f"TurboOCR failed for '{pdf_path}': {exc}",
            ) from exc

        if not result.text.strip():
            raise DocumentError(
                error_code=ErrorCode.DOCUMENT_OCR_FAILED,
                message=f"TurboOCR returned empty text for '{pdf_path}'",
            )

        lines = result.text.split("\f")
        pages: list[tuple[int, str]] = []
        for i, page_text in enumerate(lines):
            if page_text.strip():
                pages.append((i + 1, page_text.strip()))

        if not pages:
            raise DocumentError(
                error_code=ErrorCode.DOCUMENT_OCR_FAILED,
                message=f"TurboOCR produced no pages for '{pdf_path}'",
            )

        if max_pages > 0 and len(pages) > max_pages:
            pages = pages[:max_pages]

        full_text = "\n\n".join(text for _, text in pages)

        return ParsedDocument(
            text=full_text,
            pages=tuple(pages),
            page_count=len(pages),
            backend="turbo_ocr",
        )
