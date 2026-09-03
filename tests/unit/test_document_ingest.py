"""Unit tests for document processing pipeline."""

import sys
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from arrow_lake.config._enums import ChunkStrategy, OcrBackend, PdfParseMode
from arrow_lake.config.document import DocumentConfig
from arrow_lake.exceptions import DocumentError, ErrorCode
from arrow_lake.ingest.chunker import DocumentChunker, _split_by_paragraph, _split_recursive


@pytest.fixture(autouse=True)
def _clear_parse_cache():
    """[#step3-B] isolate the process-level parse cache between tests."""
    from arrow_lake.ingest.document import _PARSE_CACHE
    _PARSE_CACHE.clear()

# ---------------------------------------------------------------------------
# Mock kreuzberg module (not installed in CI / dev)
# ---------------------------------------------------------------------------
# Provide lightweight stand-ins so arrow_lake.ingest.document can import and
# the helper functions (_build_extraction_config, _result_to_pages) work
# correctly under test.


class _FakeOcrConfig:
    def __init__(self, *, backend: str = "paddleocr", language: str = "eng"):
        self.backend = backend
        self.language = language


class _FakePageConfig:
    def __init__(self, *, extract_pages: bool = False):
        self.extract_pages = extract_pages


class _FakePdfConfig:
    def __init__(self, *, extract_images: bool = False, extract_annotations: bool = True):
        self.extract_images = extract_images
        self.extract_annotations = extract_annotations


class _FakeExtractionConfig:
    def __init__(
        self,
        *,
        ocr: _FakeOcrConfig | None = None,
        force_ocr: bool = False,
        pages: _FakePageConfig | None = None,
        output_format: str = "markdown",
        pdf_options: Any | None = None,
    ):
        self.ocr = ocr
        self.force_ocr = force_ocr
        self.pages = pages
        self.output_format = output_format
        self.pdf_options = pdf_options


class _FakeKreuzbergModule:
    ExtractionConfig = _FakeExtractionConfig
    OcrConfig = _FakeOcrConfig
    PageConfig = _FakePageConfig
    PdfConfig = _FakePdfConfig
    extract_file_sync = MagicMock()


sys.modules.setdefault("kreuzberg", _FakeKreuzbergModule())


@pytest.fixture(autouse=True)
def _kreuzberg_fake_available(monkeypatch):
    """arrow_lake.ingest.document binds kreuzberg names at ITS import time —
    in the full suite the module is imported BEFORE this file installs the
    fake kreuzberg, so both the availability flag AND the
    ExtractionConfig/OcrConfig/... globals are missing. Bind all of them from
    the fake module for this file's tests (monkeypatch restores afterwards)."""
    from arrow_lake.ingest import document as _doc

    fake = sys.modules["kreuzberg"]
    monkeypatch.setattr(_doc, "_KREUZBERG_AVAILABLE", True)
    for _name in ("ExtractionConfig", "OcrConfig", "PageConfig", "PdfConfig",
                  "extract_file_sync"):
        monkeypatch.setattr(_doc, _name, getattr(fake, _name), raising=False)


# ---------------------------------------------------------------------------
# Chunker tests
# ---------------------------------------------------------------------------


class TestSplitByParagraph:
    def test_splits_on_double_newline(self):
        text = "First paragraph\n\nSecond paragraph\n\nThird paragraph"
        result = _split_by_paragraph(text)
        assert len(result) == 3
        assert result[0] == "First paragraph"

    def test_strips_whitespace(self):
        text = "  Para 1  \n\n  Para 2  "
        result = _split_by_paragraph(text)
        assert result[0] == "Para 1"
        assert result[1] == "Para 2"

    def test_empty_text(self):
        assert _split_by_paragraph("") == []

    def test_single_paragraph(self):
        result = _split_by_paragraph("Just one paragraph")
        assert result == ["Just one paragraph"]


class TestSplitRecursive:
    def test_short_text_single_chunk(self):
        result = _split_recursive("Short text", size=100, overlap=0)
        assert len(result) == 1
        assert result[0] == "Short text"

    def test_long_text_splits(self):
        text = "This is sentence one. This is sentence two. " * 20
        result = _split_recursive(text, size=50, overlap=0)
        assert len(result) > 1

    def test_overlap(self):
        text = "Word " * 100
        result = _split_recursive(text, size=50, overlap=10)
        for i in range(len(result) - 1):
            assert len(result[i]) > 0


class TestDocumentChunker:
    def test_page_strategy(self):
        pages = [(1, "Page one content"), (2, "Page two content")]
        chunker = DocumentChunker(strategy=ChunkStrategy.PAGE)
        chunks = chunker.chunk(pages)
        assert len(chunks) == 2
        assert chunks[0].text == "Page one content"
        assert chunks[0].page_number == 1

    def test_paragraph_strategy(self):
        pages = [(1, "Para one\n\nPara two\n\nPara three")]
        chunker = DocumentChunker(strategy=ChunkStrategy.PARAGRAPH)
        chunks = chunker.chunk(pages)
        assert len(chunks) == 3

    def test_recursive_strategy(self):
        text = "This is sentence one. This is sentence two. " * 20
        pages = [(1, text)]
        chunker = DocumentChunker(strategy=ChunkStrategy.RECURSIVE, chunk_size=100)
        chunks = chunker.chunk(pages)
        assert len(chunks) > 1

    def test_empty_pages(self):
        chunker = DocumentChunker()
        chunks = chunker.chunk([])
        assert chunks == []

    def test_skips_blank_pages(self):
        pages = [(1, ""), (2, "  "), (3, "Content here")]
        chunker = DocumentChunker(strategy=ChunkStrategy.PAGE)
        chunks = chunker.chunk(pages)
        assert len(chunks) == 1
        assert chunks[0].page_number == 3

    def test_sequential_chunk_indices(self):
        pages = [(1, "Para one\n\nPara two")]
        chunker = DocumentChunker(strategy=ChunkStrategy.PARAGRAPH)
        chunks = chunker.chunk(pages)
        for i, c in enumerate(chunks):
            assert c.chunk_index == i

    def test_metadata_none_by_default(self):
        chunker = DocumentChunker()
        chunks = chunker.chunk([(1, "Hello")])
        assert chunks[0].metadata is None


# ---------------------------------------------------------------------------
# DocumentConfig tests
# ---------------------------------------------------------------------------


class TestDocumentConfig:
    def test_defaults(self):
        config = DocumentConfig()
        assert config.pdf_parse_mode == PdfParseMode.AUTO
        assert config.ocr_backend == OcrBackend.KREUZBERG
        assert config.kreuzberg_ocr_backend == "paddleocr"
        assert config.kreuzberg_language == "eng"
        assert config.kreuzberg_force_ocr is False
        assert config.chunk_strategy == ChunkStrategy.RECURSIVE
        assert config.chunk_size == 512
        assert config.chunk_overlap == 64

    def test_chunk_size_validation(self):
        with pytest.raises(ValueError, match="chunk_size must be >= 64"):
            DocumentConfig(chunk_size=10)

    def test_chunk_overlap_validation(self):
        with pytest.raises(ValueError, match="chunk_overlap must be >= 0"):
            DocumentConfig(chunk_overlap=-5)

    def test_custom_values(self):
        config = DocumentConfig(
            pdf_parse_mode=PdfParseMode.TEXT,
            ocr_backend=OcrBackend.TURBO_OCR,
            chunk_strategy=ChunkStrategy.PAGE,
            max_file_size_mb=50,
        )
        assert config.pdf_parse_mode == PdfParseMode.TEXT
        assert config.ocr_backend == OcrBackend.TURBO_OCR

    def test_deprecated_marker_pdf_maps_to_kreuzberg(self):
        config = DocumentConfig(ocr_backend="marker_pdf")
        assert config.ocr_backend == OcrBackend.KREUZBERG

    def test_deprecated_pypdf_maps_to_kreuzberg(self):
        config = DocumentConfig(ocr_backend="pypdf")
        assert config.ocr_backend == OcrBackend.KREUZBERG


# ---------------------------------------------------------------------------
# Error codes
# ---------------------------------------------------------------------------


class TestDocumentErrorCodes:
    def test_document_parse_failed(self):
        assert ErrorCode.DOCUMENT_PARSE_FAILED == "DOCUMENT_PARSE_FAILED"

    def test_document_ocr_failed(self):
        assert ErrorCode.DOCUMENT_OCR_FAILED == "DOCUMENT_OCR_FAILED"

    def test_document_chunk_failed(self):
        assert ErrorCode.DOCUMENT_CHUNK_FAILED == "DOCUMENT_CHUNK_FAILED"

    def test_document_upload_failed(self):
        assert ErrorCode.DOCUMENT_UPLOAD_FAILED == "DOCUMENT_UPLOAD_FAILED"

    def test_document_error_instance(self):
        from arrow_lake.exceptions import DocumentError
        err = DocumentError(
            error_code=ErrorCode.DOCUMENT_PARSE_FAILED,
            message="test error",
        )
        assert "DOCUMENT_PARSE_FAILED" in str(err)
        assert "test error" in str(err)


# ---------------------------------------------------------------------------
# Embedding dimension validation
# ---------------------------------------------------------------------------


class TestEmbeddingDimensionValidation:
    def test_qwen3_vl_embedding_models(self):
        from arrow_lake.config.media import QWEN3_VL_EMBEDDING_MODELS
        assert "Qwen/Qwen3-VL-Embedding-2B" in QWEN3_VL_EMBEDDING_MODELS
        assert "Qwen/Qwen3-VL-Embedding-8B" in QWEN3_VL_EMBEDDING_MODELS

    def test_2b_dimension(self):
        from arrow_lake.config.media import QWEN3_VL_EMBEDDING_MODELS
        assert QWEN3_VL_EMBEDDING_MODELS["Qwen/Qwen3-VL-Embedding-2B"]["dim"] == 2048

    def test_8b_dimension(self):
        from arrow_lake.config.media import QWEN3_VL_EMBEDDING_MODELS
        assert QWEN3_VL_EMBEDDING_MODELS["Qwen/Qwen3-VL-Embedding-8B"]["dim"] == 4096

    def test_known_dimension_for_vl_model(self):
        from arrow_lake.config.media import EmbeddingConfig
        config = EmbeddingConfig(model="Qwen/Qwen3-VL-Embedding-2B")
        assert config.known_dimension == 2048

    def test_known_dimension_unknown_model(self):
        from arrow_lake.config.media import EmbeddingConfig
        config = EmbeddingConfig(model="some/unknown-model")
        assert config.known_dimension == 0

    def test_is_multimodal_vl(self):
        from arrow_lake.config.media import EmbeddingConfig
        config = EmbeddingConfig(model="Qwen/Qwen3-VL-Embedding-2B")
        assert config.is_multimodal is True

    def test_is_multimodal_non_vl(self):
        from arrow_lake.config.media import EmbeddingConfig
        config = EmbeddingConfig(model="Qwen/Qwen3-Embedding-0.6B")
        assert config.is_multimodal is False

    def test_expected_dim_field(self):
        from arrow_lake.config.media import EmbeddingConfig
        config = EmbeddingConfig(expected_dim=768)
        assert config.expected_dim == 768


# ---------------------------------------------------------------------------
# DocumentParser / Kreuzberg tests
# ---------------------------------------------------------------------------


def _mock_extraction_result(pages: list[str]):
    """Build a mock Kreuzberg ExtractionResult with the given page texts."""
    page_dicts = [
        {"page_number": i + 1, "content": text, "is_blank": not text.strip()}
        for i, text in enumerate(pages)
    ]
    mock_result = MagicMock()
    mock_result.content = "\n\n".join(t for t in pages if t.strip())
    mock_result.pages = page_dicts
    return mock_result


class TestBuildExtractionConfig:
    def test_text_mode_no_ocr(self):
        from arrow_lake.ingest.document import _build_extraction_config
        cfg = DocumentConfig()
        ext_cfg = _build_extraction_config(cfg, PdfParseMode.TEXT)
        assert ext_cfg.ocr is None
        assert ext_cfg.force_ocr is False

    def test_ocr_mode_forces_ocr(self):
        from arrow_lake.ingest.document import _build_extraction_config
        cfg = DocumentConfig()
        ext_cfg = _build_extraction_config(cfg, PdfParseMode.OCR)
        assert ext_cfg.ocr is not None
        assert ext_cfg.force_ocr is True

    def test_auto_mode_passes_ocr_config(self):
        from arrow_lake.ingest.document import _build_extraction_config
        cfg = DocumentConfig()
        ext_cfg = _build_extraction_config(cfg, PdfParseMode.AUTO)
        assert ext_cfg.ocr is not None
        assert ext_cfg.force_ocr is False

    def test_kreuzberg_force_ocr_override(self):
        from arrow_lake.ingest.document import _build_extraction_config
        cfg = DocumentConfig(kreuzberg_force_ocr=True)
        ext_cfg = _build_extraction_config(cfg, PdfParseMode.TEXT)
        assert ext_cfg.force_ocr is True
        assert ext_cfg.ocr is not None

    def test_custom_ocr_backend_and_language(self):
        from arrow_lake.ingest.document import _build_extraction_config
        cfg = DocumentConfig(kreuzberg_ocr_backend="easyocr", kreuzberg_language="eng+chi_sim")
        ext_cfg = _build_extraction_config(cfg, PdfParseMode.OCR)
        assert ext_cfg.ocr.backend == "easyocr"
        assert ext_cfg.ocr.language == "eng+chi_sim"


class TestResultToPages:
    def test_basic_conversion(self):
        from arrow_lake.ingest.document import _result_to_pages
        result = _mock_extraction_result(["Page one", "Page two", "Page three"])
        pages = _result_to_pages(result, max_pages=0)
        assert len(pages) == 3
        assert pages[0] == (1, "Page one")
        assert pages[2] == (3, "Page three")

    def test_max_pages_limit(self):
        from arrow_lake.ingest.document import _result_to_pages
        result = _mock_extraction_result(["P1", "P2", "P3", "P4", "P5"])
        pages = _result_to_pages(result, max_pages=3)
        assert len(pages) == 3

    def test_skips_empty_pages(self):
        from arrow_lake.ingest.document import _result_to_pages
        result = _mock_extraction_result(["Content", "", "   ", "More"])
        pages = _result_to_pages(result, max_pages=0)
        assert len(pages) == 2
        assert pages[0] == (1, "Content")
        assert pages[1] == (4, "More")

    def test_empty_pages_falls_back_to_content(self):
        # Non-paginated formats (markdown/txt/html/epub) return text in
        # .content with empty .pages → synthesize one page, not drop the doc.
        from arrow_lake.ingest.document import _result_to_pages
        result = MagicMock()
        result.pages = []
        result.tables = []
        result.content = "Whole document as one blob of text"
        pages = _result_to_pages(result, max_pages=0)
        assert pages == ((1, "Whole document as one blob of text"),)

    def test_empty_pages_empty_content_yields_nothing(self):
        from arrow_lake.ingest.document import _result_to_pages
        result = MagicMock()
        result.pages = []
        result.tables = []
        result.content = "   "
        pages = _result_to_pages(result, max_pages=0)
        assert pages == ()


class TestSuppressTesseractNoise:
    def test_restores_stderr_after_context(self, tmp_path) -> None:
        # Regression: the old restore was os.dup2(fd, fd) — a POSIX no-op that
        # never restored fd 2, so stderr was permanently sent to /dev/null.
        import os

        from arrow_lake.ingest.document import _suppress_tesseract_noise

        sentinel = tmp_path / "err.log"
        fd = os.open(str(sentinel), os.O_WRONLY | os.O_CREAT | os.O_TRUNC)
        saved = os.dup(2)
        os.dup2(fd, 2)  # fd 2 → sentinel
        try:
            with _suppress_tesseract_noise():
                os.write(2, b"INSIDE\n")  # suppressed → /dev/null
            os.write(2, b"AFTER\n")  # restored → sentinel
        finally:
            os.dup2(saved, 2)
            os.close(saved)
            os.close(fd)
        content = sentinel.read_bytes()
        assert b"AFTER\n" in content  # restored write reached sentinel
        assert b"INSIDE\n" not in content  # suppressed write did not


class TestParseCache:
    """[#step3-B] identical content + config → cached; different content → miss."""

    def test_same_content_cached(self, tmp_path) -> None:
        from unittest.mock import MagicMock

        from arrow_lake.ingest.document import DocumentParser, _PARSE_CACHE

        _PARSE_CACHE.clear()
        p = DocumentParser()
        f = tmp_path / "a.pdf"
        f.write_bytes(b"%PDF same content")
        calls = [0]
        sentinel = MagicMock(name="parsed")
        p._parse_kreuzberg = lambda fp, mp: calls.__setitem__(0, calls[0] + 1) or sentinel  # type: ignore[method-assign]
        r1 = p.parse(f)
        r2 = p.parse(f)  # cache hit
        assert calls[0] == 1
        assert r1 is r2 is sentinel

    def test_different_content_misses(self, tmp_path) -> None:
        from unittest.mock import MagicMock

        from arrow_lake.ingest.document import DocumentParser, _PARSE_CACHE

        _PARSE_CACHE.clear()
        p = DocumentParser()
        f1 = tmp_path / "a.pdf"
        f1.write_bytes(b"content A")
        f2 = tmp_path / "b.pdf"
        f2.write_bytes(b"content B")
        calls = [0]
        p._parse_kreuzberg = lambda fp, mp: calls.__setitem__(0, calls[0] + 1) or MagicMock()  # type: ignore[method-assign]
        p.parse(f1)
        p.parse(f2)  # different content → miss → re-parse
        assert calls[0] == 2


class TestDocumentParser:
    @patch("arrow_lake.ingest.document.extract_file_sync")
    def test_parse_text_mode(self, mock_extract, tmp_path):
        pdf = tmp_path / "test.pdf"
        pdf.write_bytes(b"%PDF-1.4")
        mock_extract.return_value = _mock_extraction_result(["Hello world"])

        from arrow_lake.ingest.document import DocumentParser
        parser = DocumentParser(DocumentConfig(pdf_parse_mode=PdfParseMode.TEXT))
        result = parser.parse(str(pdf))

        assert result.backend == "kreuzberg"
        assert result.page_count == 1
        assert result.text == "Hello world"
        mock_extract.assert_called_once()

    @patch("arrow_lake.ingest.document.extract_file_sync")
    def test_parse_ocr_mode(self, mock_extract, tmp_path):
        pdf = tmp_path / "scan.pdf"
        pdf.write_bytes(b"%PDF-1.4")
        mock_extract.return_value = _mock_extraction_result(["OCR page 1", "OCR page 2"])

        from arrow_lake.ingest.document import DocumentParser
        parser = DocumentParser(DocumentConfig(pdf_parse_mode=PdfParseMode.OCR))
        result = parser.parse(str(pdf))

        assert result.backend == "kreuzberg"
        assert result.page_count == 2
        call_args = mock_extract.call_args
        ext_cfg = call_args.kwargs.get("config") or call_args[1].get("config")
        assert ext_cfg.force_ocr is True

    @patch("arrow_lake.ingest.document.extract_file_sync")
    def test_parse_auto_mode(self, mock_extract, tmp_path):
        pdf = tmp_path / "mixed.pdf"
        pdf.write_bytes(b"%PDF-1.4")
        mock_extract.return_value = _mock_extraction_result(["Some text"])

        from arrow_lake.ingest.document import DocumentParser
        parser = DocumentParser(DocumentConfig(pdf_parse_mode=PdfParseMode.AUTO))
        result = parser.parse(str(pdf))

        assert result.backend == "kreuzberg"
        call_args = mock_extract.call_args
        ext_cfg = call_args.kwargs.get("config") or call_args[1].get("config")
        assert ext_cfg.force_ocr is False
        assert ext_cfg.ocr is not None

    @patch("arrow_lake.ingest.document.extract_file_sync")
    def test_parse_max_pages(self, mock_extract, tmp_path):
        pdf = tmp_path / "big.pdf"
        pdf.write_bytes(b"%PDF-1.4")
        mock_extract.return_value = _mock_extraction_result(
            [f"Page {i}" for i in range(10)],
        )

        from arrow_lake.ingest.document import DocumentParser
        parser = DocumentParser(DocumentConfig(max_pages=3))
        result = parser.parse(str(pdf), max_pages=3)

        assert result.page_count == 3

    @patch("arrow_lake.ingest.document.extract_file_sync")
    def test_parse_file_not_found(self, mock_extract):
        from arrow_lake.ingest.document import DocumentParser
        parser = DocumentParser()
        with pytest.raises(FileNotFoundError, match="not found"):
            parser.parse("/nonexistent/file.pdf")

    @patch("arrow_lake.ingest.document.extract_file_sync")
    def test_parse_kreuzberg_error(self, mock_extract, tmp_path):
        pdf = tmp_path / "bad.pdf"
        pdf.write_bytes(b"not a pdf")
        mock_extract.side_effect = RuntimeError("Kreuzberg error")

        from arrow_lake.ingest.document import DocumentParser
        parser = DocumentParser()
        with pytest.raises(DocumentError, match="Kreuzberg failed"):
            parser.parse(str(pdf))

    @patch("arrow_lake.ingest.document.extract_file_sync")
    def test_parse_empty_result_raises(self, mock_extract, tmp_path):
        pdf = tmp_path / "empty.pdf"
        pdf.write_bytes(b"%PDF-1.4")
        mock_extract.return_value = _mock_extraction_result([])

        from arrow_lake.ingest.document import DocumentParser
        parser = DocumentParser()
        with pytest.raises(DocumentError, match="No text extracted"):
            parser.parse(str(pdf))


class TestDocumentParserTurboOcrFallback:
    def test_turbo_ocr_primary_available(self, tmp_path):
        pdf = tmp_path / "test.pdf"
        pdf.write_bytes(b"%PDF-1.4")

        ocr_client = MagicMock()
        ocr_client.is_available.return_value = True
        ocr_client.ocr.return_value = MagicMock(text="OCR page one\fOCR page two")

        from arrow_lake.ingest.document import DocumentParser
        parser = DocumentParser(DocumentConfig(ocr_backend=OcrBackend.TURBO_OCR))
        result = parser.parse(str(pdf), ocr_client=ocr_client)

        assert result.backend == "turbo_ocr"
        assert result.page_count == 2

    @patch("arrow_lake.ingest.document.extract_file_sync")
    def test_turbo_ocr_fallback_to_kreuzberg(self, mock_extract, tmp_path):
        pdf = tmp_path / "test.pdf"
        pdf.write_bytes(b"%PDF-1.4")

        ocr_client = MagicMock()
        ocr_client.is_available.return_value = True
        ocr_client.ocr.side_effect = ConnectionError("OCR failed")

        mock_extract.return_value = _mock_extraction_result(["Fallback text"])

        from arrow_lake.ingest.document import DocumentParser
        parser = DocumentParser(
            DocumentConfig(
                ocr_backend=OcrBackend.TURBO_OCR,
                ocr_fallback_enabled=True,
            ),
        )
        result = parser.parse(str(pdf), ocr_client=ocr_client)

        assert result.backend == "kreuzberg"

    @patch("arrow_lake.ingest.document.extract_file_sync")
    def test_turbo_ocr_unavailable_fallback_to_kreuzberg(self, mock_extract, tmp_path):
        pdf = tmp_path / "test.pdf"
        pdf.write_bytes(b"%PDF-1.4")

        ocr_client = MagicMock()
        ocr_client.is_available.return_value = False

        mock_extract.return_value = _mock_extraction_result(["Text from kreuzberg"])

        from arrow_lake.ingest.document import DocumentParser
        parser = DocumentParser(
            DocumentConfig(
                ocr_backend=OcrBackend.TURBO_OCR,
                ocr_fallback_enabled=True,
            ),
        )
        result = parser.parse(str(pdf), ocr_client=ocr_client)

        assert result.backend == "kreuzberg"
        ocr_client.ocr.assert_not_called()

    def test_turbo_ocr_no_fallback_raises(self, tmp_path):
        pdf = tmp_path / "test.pdf"
        pdf.write_bytes(b"%PDF-1.4")

        from arrow_lake.ingest.document import DocumentParser
        parser = DocumentParser(
            DocumentConfig(
                ocr_backend=OcrBackend.TURBO_OCR,
                ocr_fallback_enabled=False,
            ),
        )
        with pytest.raises(DocumentError, match="All parsing backends failed"):
            parser.parse(str(pdf))
