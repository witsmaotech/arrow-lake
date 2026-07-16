"""Document parsing pipeline — PDF extraction via Kreuzberg / TurboOCR.

Provides DocumentParser for extracting text from documents using Kreuzberg
(Rust-core, Python bindings, 91+ formats, built-in OCR) as the primary engine,
with optional TurboOCR GPU fallback.
"""

from __future__ import annotations

import contextlib
import logging
import os
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

from arrow_lake.config._enums import OcrBackend, PdfParseMode
from arrow_lake.config.document import DocumentConfig
from arrow_lake.exceptions import DocumentError, ErrorCode

try:
    from kreuzberg import ExtractionConfig, OcrConfig, PageConfig, PdfConfig, extract_file_sync

    _KREUZBERG_AVAILABLE = True
except ImportError:
    _KREUZBERG_AVAILABLE = False
    ExtractionConfig = None  # type: ignore[assignment, misc]
    OcrConfig = None  # type: ignore[assignment, misc]
    PageConfig = None  # type: ignore[assignment, misc]
    PdfConfig = None  # type: ignore[assignment, misc]
    extract_file_sync = None  # type: ignore[assignment]

try:
    from docling.document_converter import (
        DocumentConverter,
        ImageFormatOption,
        PdfFormatOption,
    )
    from docling.datamodel.base_models import InputFormat
    from docling.datamodel.pipeline_options import (
        EasyOcrOptions,
        PdfPipelineOptions,
        RapidOcrOptions,
        TesseractOcrOptions,
        VlmConvertOptions,
        VlmPipelineOptions,
    )
    from docling.datamodel.vlm_engine_options import TransformersVlmEngineOptions
    from docling.pipeline.vlm_pipeline import VlmPipeline

    _DOCLING_AVAILABLE = True
except ImportError:
    _DOCLING_AVAILABLE = False
    DocumentConverter = None  # type: ignore[assignment]
    PdfFormatOption = None  # type: ignore[assignment]
    ImageFormatOption = None  # type: ignore[assignment]
    InputFormat = None  # type: ignore[assignment]
    PdfPipelineOptions = None  # type: ignore[assignment]
    RapidOcrOptions = None  # type: ignore[assignment]
    EasyOcrOptions = None  # type: ignore[assignment]
    TesseractOcrOptions = None  # type: ignore[assignment]
    VlmPipelineOptions = None  # type: ignore[assignment]
    VlmConvertOptions = None  # type: ignore[assignment]
    TransformersVlmEngineOptions = None  # type: ignore[assignment]
    VlmPipeline = None  # type: ignore[assignment]

if TYPE_CHECKING:
    pass

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
        docling_doc: DoclingDocument 对象（仅 backend="docling" 时），
            供 HybridChunker 等结构感知分块器消费；其他后端为 None。
    """

    text: str
    pages: tuple[tuple[int, str], ...]
    page_count: int
    backend: str
    blob_key: str = ""
    docling_doc: Any = None


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
        output_format="markdown",
        pdf_options=PdfConfig(extract_images=False, extract_annotations=True),
    )


def _result_to_pages(
    result: Any, max_pages: int,
) -> tuple[tuple[int, str], ...]:
    """Convert Kreuzberg ExtractionResult to ParsedDocument pages format.

    Kreuzberg returns pages as list of dicts: {"page_number": int, "content": str, ...}.
    Tables are extracted separately and appended to their source pages.
    """
    tables_by_page: dict[int, list[str]] = {}
    for table in getattr(result, "tables", None) or []:
        t_page = getattr(table, "page_number", 0)
        t_md = getattr(table, "markdown", "") or ""
        if t_page and t_md:
            tables_by_page.setdefault(t_page, []).append(t_md)

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
            extra = tables_by_page.pop(page_num, [])
            if extra:
                text = text + "\n\n" + "\n\n".join(extra)
            pages.append((page_num, text))

        if max_pages > 0 and len(pages) >= max_pages:
            break

    # Non-paginated formats (markdown, plain text, HTML, EPUB, ...): kreuzberg
    # returns the whole text in ``result.content`` with an empty ``result.pages``
    # list. Synthesize a single page so the chunker sees the text instead of
    # silently dropping the document as zero chunks.
    if not pages:
        content = (getattr(result, "content", "") or "").strip()
        if content:
            pages.append((1, content))
    return tuple(pages)


@contextlib.contextmanager
def _suppress_tesseract_noise():
    """Suppress tesseract stderr noise from kreuzberg's Rust core.

    Kreuzberg's paddleocr backend may internally invoke tesseract as a
    fallback, which emits noisy errors when tessdata is missing.
    """
    # Save the real stderr (fd 2) BEFORE redirecting, then restore it in the
    # finally. The previous restore was os.dup2(fd, fd) — a POSIX no-op — and
    # never saved fd 2, so the process stderr was permanently sent to /dev/null
    # after the first parse (silent loss of all tracebacks / subprocess errors
    # in the long-running API container).
    saved_stderr = os.dup(2)
    devnull = os.open(os.devnull, os.O_WRONLY)
    try:
        os.dup2(devnull, 2)
        yield
    finally:
        os.dup2(saved_stderr, 2)
        os.close(devnull)
        os.close(saved_stderr)


# [#step3-B] Process-level parse cache: identical file content + identical parse
# config → reuse the ParsedDocument (avoids re-parse + re-OCR on re-ingest of an
# unchanged file). Bounded LRU (count) so memory stays capped. ParsedDocument is
# treated as immutable (downstream reads pages/text, never mutates).
import threading as _threading
from collections import OrderedDict as _OrderedDict

_PARSE_CACHE: _OrderedDict = _OrderedDict()
_PARSE_CACHE_MAX = 32
_PARSE_CACHE_LOCK = _threading.Lock()


def _parse_cache_get(key):
    with _PARSE_CACHE_LOCK:
        v = _PARSE_CACHE.get(key)
        if v is not None:
            _PARSE_CACHE.move_to_end(key)
        return v


def _parse_cache_put(key, value):
    with _PARSE_CACHE_LOCK:
        _PARSE_CACHE[key] = value
        while len(_PARSE_CACHE) > _PARSE_CACHE_MAX:
            _PARSE_CACHE.popitem(last=False)


# [#audit-P1/P2] Process-level Docling DocumentConverter cache. DocumentParser is
# recreated per ingest request, so an instance-level converter reloaded the
# layout/table/OCR models on every request (10-30s). Keyed by a config signature
# → the (expensive) converter is built once per distinct config and shared. Each
# entry carries its own RLock guarding ``convert()``: Docling inference is not
# guaranteed thread-safe, and the router serves concurrent ingests from a thread
# pool, so parses on the SAME converter are serialized (different converters still
# run in parallel). Unbounded — distinct configs per process are few.
_DOCLING_CONVERTERS: dict[tuple, tuple[Any, _threading.RLock]] = {}
_DOCLING_BUILD_LOCK = _threading.Lock()


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
        # Docling DocumentConverter is now a process-level singleton keyed by
        # config signature (see _get_docling_converter); no instance cache.

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

        # [#step3-B] parse cache: identical content + config → reuse ParsedDocument
        import hashlib
        _obe = self._config.ocr_backend
        cache_key = (
            hashlib.sha256(file_path.read_bytes()).hexdigest(),
            getattr(_obe, "value", str(_obe)),
            str(self._config.pdf_parse_mode),
            effective_max,
        )
        _cached = _parse_cache_get(cache_key)
        if _cached is not None:
            return _cached

        if self._config.ocr_backend == OcrBackend.TURBO_OCR:
            result = self._parse_turbo_ocr_primary(file_path, ocr_client, effective_max)
        elif self._config.ocr_backend == OcrBackend.DOCLING:
            result = self._parse_docling(file_path, effective_max)
        else:
            result = self._parse_kreuzberg(file_path, effective_max)

        _parse_cache_put(cache_key, result)
        return result

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
            with _suppress_tesseract_noise():
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

    def _docling_signature(self) -> tuple:
        """Hashable signature of the config fields that affect the converter.

        Two configs with the same signature share one DocumentConverter (and its
        loaded models); a differing field yields a separate converter.
        """
        cfg = self._config
        langs = getattr(cfg, "docling_ocr_languages", None) or []
        return (
            str(getattr(cfg, "docling_pipeline_type", None)),
            str(getattr(cfg, "docling_vlm_preset", None)),
            str(getattr(cfg, "docling_ocr_engine", None)),
            tuple(str(x) for x in langs),
        )

    def _build_docling_converter(self) -> Any:
        """Construct a fresh Docling DocumentConverter (expensive — loads models).

        Called at most once per distinct config signature (see
        ``_get_docling_converter``); callers must never invoke this directly to
        avoid reloading layout/table/OCR models on every request.
        """
        from arrow_lake.config._enums import DoclingPipelineType

        # VLM 流水线（GraniteDocling）：端到端视觉模型，复杂版面/扫描件/公式。
        # 与标准流水线互斥——VLM 独占 PDF/IMAGE 的 pipeline_cls。
        if self._config.docling_pipeline_type == DoclingPipelineType.VLM:
            pdf_option = PdfFormatOption(
                pipeline_cls=VlmPipeline,
                pipeline_options=self._build_docling_vlm_pipeline(),
            )
            return DocumentConverter(
                allowed_formats=[InputFormat.PDF, InputFormat.IMAGE],
                format_options={
                    InputFormat.PDF: pdf_option,
                    InputFormat.IMAGE: pdf_option,
                },
            )

        # 标准流水线：布局 + OCR + 表格识别
        engine, langs = self._resolve_docling_ocr()
        pipeline = self._build_docling_pipeline(engine, langs)
        # 多格式默认首选：PDF/IMAGE 用配好的 pipeline(OCR)，
        # DOCX/PPTX/XLSX/HTML/MD/ASCIIDOC 用默认 SimplePipeline（无需 layout 模型，快）
        allowed = [
            getattr(InputFormat, n) for n in (
                "PDF", "DOCX", "PPTX", "XLSX", "HTML", "IMAGE", "MD", "ASCIIDOC",
            ) if getattr(InputFormat, n, None) is not None
        ]
        return DocumentConverter(
            allowed_formats=allowed,
            format_options={
                InputFormat.PDF: PdfFormatOption(pipeline_options=pipeline),
                InputFormat.IMAGE: ImageFormatOption(pipeline_options=pipeline),
            },
        )

    def _get_docling_converter(self) -> tuple[Any, _threading.RLock]:
        """Return the process-shared ``(converter, convert_lock)`` for this config.

        The converter (with its loaded models) is built once per distinct config
        signature and cached at module level — DocumentParser is recreated per
        ingest request, so an instance attribute would reload the models every
        time. The returned RLock guards ``converter.convert()`` for thread-safety
        under concurrent ingest (Docling inference is not re-entrant).
        """
        if not _DOCLING_AVAILABLE:
            raise DocumentError(
                error_code=ErrorCode.DOCUMENT_PARSE_FAILED,
                message="docling package is not installed. Install with: pip install arrow-lake[docling]",
            )
        sig = self._docling_signature()
        cached = _DOCLING_CONVERTERS.get(sig)
        if cached is not None:
            return cached
        # Double-checked locking: build is expensive (model load), serialize it.
        with _DOCLING_BUILD_LOCK:
            cached = _DOCLING_CONVERTERS.get(sig)
            if cached is not None:
                return cached
            converter = self._build_docling_converter()
            entry = (converter, _threading.RLock())
            _DOCLING_CONVERTERS[sig] = entry
            return entry

    def _build_docling_vlm_pipeline(self) -> Any:
        """构造 Docling VlmPipelineOptions（GraniteDocling 端到端视觉模型，本地 Transformers）。

        preset 默认 granite_docling（258M，DocTags 输出）；模型从 HF_HOME 卷加载，
        CPU 可跑（慢，~100s/页），有 GPU 则快。换 preset/runt­ime 见 ADR §P2。
        """
        preset = self._config.docling_vlm_preset or "granite_docling"
        engine = TransformersVlmEngineOptions()
        vlm_options = VlmConvertOptions.from_preset(preset, engine_options=engine)
        return VlmPipelineOptions(vlm_options=vlm_options)

    def _parse_docling(
        self, file_path: Path, max_pages: int,
    ) -> ParsedDocument:
        """Parse via Docling Python SDK（库内嵌，多格式 + 可插拔 OCR）。

        多格式（PDF/Office/HTML/图片/邮件）+ OCR（rapidocr 中文 / easyocr 多语言 /
        tesseract 英文）。详见 ADR docs/docling-ocr-migration-adr.md。
        """
        converter, convert_lock = self._get_docling_converter()
        try:
            # Serialize convert() per-converter: Docling inference (layout/OCR/
            # table models) is not guaranteed thread-safe, and the router serves
            # concurrent ingests from a thread pool sharing this converter.
            with convert_lock:
                result = converter.convert(str(file_path))
        except Exception as exc:
            raise DocumentError(
                error_code=ErrorCode.DOCUMENT_PARSE_FAILED,
                message=f"Docling failed to parse '{file_path}': {exc}",
            ) from exc

        doc = result.document
        md = doc.export_to_markdown() or ""
        # 置信度评估（docling v2.34+: mean_grade / low_grade），用于摄入质量门控
        conf = getattr(result, "confidence", None)
        if conf is not None:
            logger.info(
                "docling confidence file=%s mean_grade=%s low_grade=%s",
                file_path, getattr(conf, "mean_grade", None), getattr(conf, "low_grade", None),
            )
        if not md:
            raise DocumentError(
                error_code=ErrorCode.DOCUMENT_PARSE_FAILED,
                message=f"Docling returned empty content for '{file_path}'",
            )

        # 按真实页码拆分（item.prov[0].page_no）。此前把整篇 markdown 塞进 page 1，
        # 导致 max_pages 无法切片、chunk 的 page_number 全为 1。
        pages_map: dict[int, list[str]] = {}
        for item in getattr(doc, "texts", None) or []:
            provs = getattr(item, "prov", None) or []
            page_no = provs[0].page_no if provs else (len(pages_map) + 1)
            txt = getattr(item, "text", None) or ""
            if txt:
                pages_map.setdefault(page_no, []).append(txt)
        for tbl in getattr(doc, "tables", None) or []:
            provs = getattr(tbl, "prov", None) or []
            page_no = provs[0].page_no if provs else (len(pages_map) + 1)
            try:
                tmd = tbl.export_to_markdown(doc=doc) if callable(getattr(tbl, "export_to_markdown", None)) else ""
            except Exception:
                tmd = ""
            if tmd:
                pages_map.setdefault(page_no, []).append(tmd)
        pages: list[tuple[int, str]] = []
        for page_no, parts in sorted(pages_map.items(), key=lambda kv: kv[0]):
            page_text = "\n\n".join(parts).strip()
            if page_text:
                pages.append((page_no, page_text))
                if max_pages > 0 and len(pages) >= max_pages:
                    break
        if not pages:  # 拆页失败兜底：退回整篇，不丢数据
            pages = [(1, md)]
        # docling_doc 透传 DoclingDocument 对象，供 HybridChunker 结构感知分块消费。
        return ParsedDocument(
            text=md,
            pages=tuple(pages),
            page_count=len(pages),
            backend="docling",
            docling_doc=doc,
        )

    def _resolve_docling_ocr(self) -> tuple[str, list[str]]:
        """自动选择 docling OCR 引擎：中文→rapidocr，多语言→easyocr，默认 rapidocr。

        rapidocr 用 PaddleOCR PP-OCRv4 模型，#3569 在纯中文场景强制中文模型正合适；
        显式指定非中文语言时切 easyocr（多语言可控）。
        """
        from arrow_lake.config._enums import DoclingOcrEngine

        cfg = self._config
        engine = cfg.docling_ocr_engine
        if engine != DoclingOcrEngine.AUTO:
            return engine.value, list(cfg.docling_ocr_languages)
        langs = cfg.docling_ocr_languages
        if langs and "ch_sim" not in langs:
            return DoclingOcrEngine.EASYOCR.value, list(langs)
        return DoclingOcrEngine.RAPIDOCR.value, list(langs) or ["ch_sim"]

    @staticmethod
    def _build_docling_pipeline(engine: str, langs: list[str]) -> Any:
        """构造 Docling PdfPipelineOptions（OCR 引擎切换 + 表格识别优化）。

        表格：TableFormerMode.ACCURATE + do_cell_matching=False，
        针对中文工程文档多列表格（目录/投资表）防 cell 错误合并。
        """
        if not _DOCLING_AVAILABLE:
            return None
        # OCR 引擎选择（auto 默认 rapidocr）
        if engine == "easyocr":
            ocr = EasyOcrOptions(lang=langs or ["ch_sim", "en"])
        elif engine == "tesseract":
            ocr = TesseractOcrOptions(lang=langs or ["eng"])
        else:  # rapidocr / auto / none（none 仍保留 ocr_options 默认，由 do_ocr=False 关闭）
            ocr = RapidOcrOptions()
        pipeline = PdfPipelineOptions(
            do_ocr=(engine != "none"), ocr_options=ocr, do_table_structure=True,
        )
        # 表格识别优化（中文多列防错并）
        try:
            from docling.datamodel.pipeline_options import TableFormerMode
            pipeline.table_structure_options.mode = TableFormerMode.ACCURATE
            pipeline.table_structure_options.do_cell_matching = False
        except Exception as e:
            logger.debug("TableFormerMode config skipped: %s", e)
        # 硬件加速：有 CUDA 时显式用 GPU（layout-heron + TableFormer 提速约一个数量级）；
        # 否则交由 docling AUTO 选 CPU/MPS。CPU 镜像无 GPU 时安全回退。
        try:
            import torch
            from docling.datamodel.accelerator_options import AcceleratorDevice, AcceleratorOptions
            device = AcceleratorDevice.CUDA if torch.cuda.is_available() else AcceleratorDevice.AUTO
            pipeline.accelerator_options = AcceleratorOptions(device=device)
            logger.info("docling_accelerator device=%s", device.value)
        except Exception as e:
            logger.debug("docling accelerator config skipped: %s", e)
        return pipeline

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
