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
        RapidOcrOptions,
        TesseractOcrOptions,
        ThreadedPdfPipelineOptions,
        VlmConvertOptions,
        VlmPipelineOptions,
    )
    from docling.datamodel.settings import settings as _docling_settings
    from docling.datamodel.vlm_engine_options import TransformersVlmEngineOptions
    from docling.pipeline.vlm_pipeline import VlmPipeline

    _DOCLING_AVAILABLE = True
except ImportError:
    _DOCLING_AVAILABLE = False
    DocumentConverter = None  # type: ignore[assignment]
    PdfFormatOption = None  # type: ignore[assignment]
    ImageFormatOption = None  # type: ignore[assignment]
    InputFormat = None  # type: ignore[assignment]
    RapidOcrOptions = None  # type: ignore[assignment]
    EasyOcrOptions = None  # type: ignore[assignment]
    TesseractOcrOptions = None  # type: ignore[assignment]
    VlmPipelineOptions = None  # type: ignore[assignment]
    VlmConvertOptions = None  # type: ignore[assignment]
    ThreadedPdfPipelineOptions = None  # type: ignore[assignment]
    _docling_settings = None  # type: ignore[assignment]
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
# Per-document Docling parse ceiling. Docling inference has no built-in
# timeout; a pathological PDF or a GPU/CUDA stall can hang convert() for hours
# (incident: worker stuck 2.5h). Python can't kill the leaked thread, but the
# timeout lets us mark the file failed and evict the poisoned converter so
# later parses aren't queued behind the hung one. Tunable via env.
_DOCLING_CONVERT_TIMEOUT = float(os.environ.get("ARROW_LAKE_DOCLING_TIMEOUT_SECONDS", "1200"))


class _DoclingConvertTimeout(Exception):
    """Raised when ``converter.convert()`` exceeds the per-document ceiling."""


def _convert_with_timeout(
    converter: Any, path: str, lock: Any, timeout: float,
    page_range: tuple[int, int] | None = None,
) -> Any:
    """Run ``converter.convert(path)`` under ``lock`` in a daemon worker; bound it.

    Docling inference isn't thread-safe, so convert runs under the converter's
    RLock (acquired inside the worker). On timeout the worker is leaked (Python
    cannot kill threads) — the caller must evict the poisoned converter entry so
    subsequent parses get a fresh converter + lock instead of queueing behind
    the hung thread. ``page_range`` (1-based inclusive) is forwarded to
    ``convert()`` for chunked conversion of large docs.
    """
    holder: dict[str, Any] = {}

    def _work() -> None:
        with lock:
            try:
                if page_range is not None:
                    holder["result"] = converter.convert(path, page_range=page_range)
                else:
                    holder["result"] = converter.convert(path)
            except BaseException as exc:  # noqa: BLE001 — surfaced to caller
                holder["error"] = exc

    t = _threading.Thread(target=_work, daemon=True, name="docling-convert")
    t.start()
    t.join(timeout)
    if t.is_alive():
        raise _DoclingConvertTimeout(f"convert exceeded {timeout}s")
    if "error" in holder:
        raise holder["error"]
    return holder.get("result")


# Kreuzberg extraction has no built-in timeout; a pathological file (or an
# internal OCR subprocess hang) can block the parse indefinitely. Same daemon-
# worker pattern as docling. Tunable via env.
_KREUZBERG_TIMEOUT = float(os.environ.get("ARROW_LAKE_KREUZBERG_TIMEOUT_SECONDS", "600"))


def _run_with_timeout(func: Any, timeout: float, label: str) -> Any:
    """Run ``func()`` in a daemon worker bounded by ``timeout`` (builtin).

    Raises ``TimeoutError`` on timeout (a subclass of OSError, so callers that
    catch OSError must catch TimeoutError first). The worker is leaked on
    timeout — Python can't kill threads.
    """
    holder: dict[str, Any] = {}

    def _work() -> None:
        try:
            holder["result"] = func()
        except BaseException as exc:  # noqa: BLE001 — surfaced to caller
            holder["error"] = exc

    t = _threading.Thread(target=_work, daemon=True, name=f"parse-{label}")
    t.start()
    t.join(timeout)
    if t.is_alive():
        raise TimeoutError(f"{label} exceeded {timeout}s")
    if "error" in holder:
        raise holder["error"]
    return holder.get("result")


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
                result = _run_with_timeout(
                    lambda: extract_file_sync(str(file_path), config=cfg),  # type: ignore[misc]
                    timeout=_KREUZBERG_TIMEOUT, label="kreuzberg",
                )
        except TimeoutError:
            raise DocumentError(
                error_code=ErrorCode.DOCUMENT_PARSE_FAILED,
                message=f"Kreuzberg parse timed out after {_KREUZBERG_TIMEOUT}s for '{file_path}'",
            )
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
        """Parse via Docling Python SDK（库内嵌，多格式 + 可插拔 OCR + 大文档分块）。

        多格式（PDF/Office/HTML/图片/邮件）+ OCR（rapidocr 中文 / easyocr 多语言 /
        tesseract 英文）。v1.10.3 起大文档自动分块转换(``docling_chunk_size`` 页一块),
        把 convert() 的页栅格+推理张量内存从 O(总页数) 降到 O(块大小),解 552 页 OOM
        (实测 6.2min / 1.48 p/s / 无 OOM)。详见 ADR docs/docling-ocr-migration-adr.md。
        """
        converter, convert_lock = self._get_docling_converter()
        chunk_size = int(getattr(self._config, "docling_chunk_size", 0) or 0)

        try:
            if chunk_size <= 0:
                # escape hatch:原单次 convert 路径(不分块)
                result = _convert_with_timeout(
                    converter, str(file_path), convert_lock, _DOCLING_CONVERT_TIMEOUT,
                )
                docs = [result.document]
                conf = getattr(result, "confidence", None)
                del result
            else:
                # 分块路径:page_range 切片,每块 convert 释放后再下一块,内存 O(块大小)
                docs, conf = self._convert_docling_chunked(
                    converter, convert_lock, file_path, chunk_size, max_pages,
                )
        except _DoclingConvertTimeout:
            _DOCLING_CONVERTERS.pop(self._docling_signature(), None)
            logger.warning(
                "docling convert timed out after %ss: %s", _DOCLING_CONVERT_TIMEOUT, file_path,
            )
            raise DocumentError(
                error_code=ErrorCode.DOCUMENT_PARSE_FAILED,
                message=f"Docling parse timed out after {_DOCLING_CONVERT_TIMEOUT}s for '{file_path}'",
            )
        except Exception as exc:
            raise DocumentError(
                error_code=ErrorCode.DOCUMENT_PARSE_FAILED,
                message=f"Docling failed to parse '{file_path}': {exc}",
            ) from exc

        if not docs:
            raise DocumentError(
                error_code=ErrorCode.DOCUMENT_PARSE_FAILED,
                message=f"Docling returned empty content for '{file_path}'",
            )

        # 合并分块 DoclingDocument(concatenate)→ 单 doc,下游 page-building + HybridChunker 统一处理
        merged = self._merge_docling_docs(docs)
        # 置信度评估(docling v2.34+: mean_grade / low_grade),用于摄入质量门控
        if conf is not None:
            logger.info(
                "docling confidence file=%s mean_grade=%s low_grade=%s",
                file_path, getattr(conf, "mean_grade", None), getattr(conf, "low_grade", None),
            )
        return self._build_parsed_from_docling(merged, docs, max_pages, file_path)

    def _convert_docling_chunked(
        self, converter: Any, convert_lock: Any, file_path: Path,
        chunk_size: int, max_pages: int,
    ) -> tuple[list[Any], Any]:
        """分块 convert:page_range 切片循环,每块释放后再下一块。返 (chunk_docs, last_confidence)。

        内存有界:convert() 的页栅格+推理张量在 convert 返回后释放(每块 ``del result``);
        仅保留轻量 DoclingDocument items 供末尾 concatenate。小文档自然=1 块
        (page_range 覆盖全文,等价单次 convert)。
        """
        import gc
        docs: list[Any] = []
        last_conf: Any = None
        start = 1
        pages_done = 0
        while True:
            end = start + chunk_size - 1
            result = _convert_with_timeout(
                converter, str(file_path), convert_lock, _DOCLING_CONVERT_TIMEOUT,
                page_range=(start, end),
            )
            doc = result.document
            last_conf = getattr(result, "confidence", None) or last_conf
            n = self._docling_page_count(doc)
            del result
            gc.collect()
            if n == 0:
                break  # 超过文档末尾
            docs.append(doc)
            pages_done += n
            if n < chunk_size:
                break  # 末尾不满一块 = 文档结束
            if max_pages > 0 and pages_done >= max_pages:
                break
            start += chunk_size
        logger.info(
            "docling_chunked file=%s chunks=%d pages~%d chunk_size=%d",
            file_path, len(docs), pages_done, chunk_size,
        )
        return docs, last_conf

    @staticmethod
    def _docling_page_count(doc: Any) -> int:
        """doc.num_pages is a method in docling 2.x; call defensively."""
        attr = getattr(doc, "num_pages", None)
        try:
            if callable(attr):
                return int(attr())
            if isinstance(attr, int):
                return attr
        except Exception:
            pass
        return 0

    @staticmethod
    def _merge_docling_docs(docs: list[Any]) -> Any:
        """合并分块 DoclingDocument 为单个(``DoclingDocument.concatenate``)。

        多块调 concatenate(官方合并,产出页号一致的单 doc);单块直接返回;
        失败返 None → 下游 _build_parsed_from_docling 降级为逐块 markdown 拼接
        (HybridChunker 退化为 RECURSIVE,文本不丢)。
        """
        if not docs:
            return None
        if len(docs) == 1:
            return docs[0]
        try:
            from docling_core.types.doc import DoclingDocument
            return DoclingDocument.concatenate(docs)
        except Exception as exc:
            logger.warning(
                "docling concatenate failed (HybridChunker 将降级 RECURSIVE): %s", exc,
            )
            return None

    @staticmethod
    def _build_parsed_from_docling(
        merged: Any, docs: list[Any], max_pages: int, file_path: Path,
    ) -> ParsedDocument:
        """从(合并后的)DoclingDocument 构建 ParsedDocument:markdown + 按真实页码拆分。

        merged 非空时优先用(concatenate 后的单 doc,页号一致,HybridChunker 拿完整结构);
        merged=None(concatenate 失败)时退化为遍历各块 doc 拼接 markdown。按 item.prov[0].page_no
        拆页——此前把整篇 markdown 塞进 page 1,导致 max_pages 无法切片、chunk page_number 全为 1。
        """
        pages_map: dict[int, list[str]] = {}
        if merged is not None:
            md = merged.export_to_markdown() or ""
            src_docs = [merged]
        else:
            # fallback:concatenate 失败,逐块拼 markdown(丢精确页号保文本)
            md = "\n\n".join((d.export_to_markdown() or "") for d in docs).strip()
            src_docs = docs
        for doc in src_docs:
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
        if not md:
            raise DocumentError(
                error_code=ErrorCode.DOCUMENT_PARSE_FAILED,
                message=f"Docling returned empty content for '{file_path}'",
            )
        if not pages:  # 拆页失败兜底:退回整篇,不丢数据
            pages = [(1, md)]
        # docling_doc 透传(merged 或 None),供 HybridChunker 结构感知分块消费。
        return ParsedDocument(
            text=md,
            pages=tuple(pages),
            page_count=len(pages),
            backend="docling",
            docling_doc=merged,
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
        """构造 Docling ThreadedPdfPipelineOptions（GPU 页批处理 + OCR 引擎 + 表格优化）。

        v1.10.3: plain ``PdfPipelineOptions``(逐页串行)→ ``ThreadedPdfPipelineOptions``
        (文档内多页打包喂 layout/OCR/table 模型)。M0 实测 7.4× 加速(20 页 126s→17s,
        见 tests/benchmark/docling_convert_bench.py),质量无损。OCR 仍 ONNX CPU(R1:
        RapidOCR torch 后端需 .pth 模型,read-only 镜像写不了;且批处理后 OCR 非瓶颈)。

        表格:TableFormerMode.ACCURATE + do_cell_matching=False,中文工程文档多列防错并。
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
        # GPU 页批处理:layout/table 模型批处理是主要加速来源(M0 实证)。
        # batch_size 经 env 可调(显存小机器下调);CPU 无 GPU 时 Threaded 退化为小批串行,不会更慢。
        try:
            _layout_batch = int(os.environ.get("ARROW_LAKE_DOCLING_LAYOUT_BATCH", "64"))
            _ocr_batch = int(os.environ.get("ARROW_LAKE_DOCLING_OCR_BATCH", "64"))
            _table_batch = int(os.environ.get("ARROW_LAKE_DOCLING_TABLE_BATCH", "4"))
        except ValueError:
            _layout_batch, _ocr_batch, _table_batch = 64, 64, 4
        pipeline = ThreadedPdfPipelineOptions(
            do_ocr=(engine != "none"), ocr_options=ocr, do_table_structure=True,
            ocr_batch_size=_ocr_batch, layout_batch_size=_layout_batch,
            table_batch_size=_table_batch,
        )
        # page_batch_size:docling 全局并发页数(默认 4)。v1.10.3 默认 16——M0 实测 page_batch=64
        # 在 552 页全量 + OCR-on-CPU 下 OOM-killed 整个 16GiB api 容器(64 页并发 × 页栅格 +
        # ONNX OCR 张量撑爆宿主 RAM)。16 在 16GiB 容器安全;大内存机器 env 调高(32/64)。
        # 根因缓解待 P0-2(OCR 上 GPU,张量从 RAM 挪 VRAM,腾 RAM 才能用大 batch)。
        if _docling_settings is not None:
            try:
                _docling_settings.perf.page_batch_size = int(
                    os.environ.get("ARROW_LAKE_DOCLING_PAGE_BATCH", "16")
                )
            except (ValueError, AttributeError):
                pass
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
            logger.info(
                "docling_accelerator device=%s layout_batch=%d ocr_batch=%d table_batch=%d",
                device.value, _layout_batch, _ocr_batch, _table_batch,
            )
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
