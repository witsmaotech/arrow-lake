"""Unit tests for P2 docling features — VlmPipeline (GraniteDocling) + HybridChunker.

docling is an optional extra (not installed in dev/CI). These tests verify the
wiring/config/dispatch logic by patching module-level availability flags and
injecting fake docling modules for lazy imports. AAA pattern throughout.
"""

from __future__ import annotations

import sys
import types
from typing import Any
from unittest.mock import MagicMock

import pytest

from arrow_lake.config._enums import ChunkStrategy, DoclingPipelineType
from arrow_lake.config.document import DocumentConfig
from arrow_lake.ingest import chunker as chunker_mod
from arrow_lake.ingest import document as document_mod
from arrow_lake.ingest.chunker import DocumentChunker
from arrow_lake.ingest.document import ParsedDocument

# 注意：test_chunker_advanced.py 用 importlib.reload(chunker_mod)，会重建 Chunk 类对象。
# 因此 isinstance / 实例化必须通过 chunker_mod.* 动态读取（模块字典当前值），
# 不能用收集期 from-import 的旧引用，否则跨 reload 的类身份不匹配。


# ---------------------------------------------------------------------------
# Config tests (no docling needed)
# ---------------------------------------------------------------------------


class TestDoclingP2Config:
    def test_pipeline_type_defaults_to_standard(self):
        # Arrange / Act
        cfg = DocumentConfig()
        # Assert
        assert cfg.docling_pipeline_type == DoclingPipelineType.STANDARD

    def test_pipeline_type_accepts_vlm(self):
        # Arrange
        cfg = DocumentConfig(docling_pipeline_type="vlm")
        # Act / Assert
        assert cfg.docling_pipeline_type == DoclingPipelineType.VLM

    def test_vlm_preset_default_is_granite_docling(self):
        # Arrange / Act
        cfg = DocumentConfig()
        # Assert
        assert cfg.docling_vlm_preset == "granite_docling"

    def test_chunk_tokenizer_default_aligns_with_bge_m3(self):
        # Arrange / Act
        cfg = DocumentConfig()
        # Assert — 与默认嵌入模型 bge-m3 对齐
        assert cfg.docling_chunk_tokenizer == "BAAI/bge-m3"

    def test_chunk_strategy_enum_has_docling_hybrid(self):
        # Arrange / Act / Assert
        assert ChunkStrategy.DOCLING_HYBRID.value == "docling_hybrid"


# ---------------------------------------------------------------------------
# ParsedDocument.docling_doc field
# ---------------------------------------------------------------------------


class TestParsedDocumentDoclingDoc:
    def test_docling_doc_defaults_to_none(self):
        # Arrange / Act
        pd = ParsedDocument(text="x", pages=((1, "x"),), page_count=1, backend="kreuzberg")
        # Assert
        assert pd.docling_doc is None

    def test_docling_doc_holds_arbitrary_object(self):
        # Arrange
        sentinel = object()
        # Act
        pd = ParsedDocument(
            text="x", pages=((1, "x"),), page_count=1, backend="docling", docling_doc=sentinel,
        )
        # Assert
        assert pd.docling_doc is sentinel


# ---------------------------------------------------------------------------
# VlmPipeline builder — patch module globals (docling not installed in dev)
# ---------------------------------------------------------------------------


class _CapturePdfFormatOption:
    """Fake PdfFormatOption that records how it was constructed."""

    instances: list["_CapturePdfFormatOption"] = []

    def __init__(self, *, pipeline_options: Any = None, pipeline_cls: Any = None) -> None:
        self.pipeline_options = pipeline_options
        self.pipeline_cls = pipeline_cls
        _CapturePdfFormatOption.instances.append(self)


class _CaptureImageFormatOption:
    def __init__(self, *, pipeline_options: Any = None) -> None:
        self.pipeline_options = pipeline_options


class _FakeVlmEngineOpts:
    def __init__(self, **kwargs: Any) -> None:
        self.kwargs = kwargs


class _FakeVlmConvertOpts:
    """Records from_preset args; vlm_options wraps it."""

    last_preset: str | None = None
    last_engine: Any = None

    @classmethod
    def from_preset(cls, name: str, *, engine_options: Any = None) -> "_FakeVlmConvertOpts":
        cls.last_preset = name
        cls.last_engine = engine_options
        return cls()


class _FakeVlmPipelineOpts:
    def __init__(self, *, vlm_options: Any = None) -> None:
        self.vlm_options = vlm_options


class _FakeInputFormat:
    PDF = "PDF"
    IMAGE = "IMAGE"
    DOCX = "DOCX"
    PPTX = "PPTX"
    XLSX = "XLSX"
    HTML = "HTML"
    MD = "MD"
    ASCIIDOC = "ASCIIDOC"


class _FakeDocumentConverter:
    """Captures allowed_formats + format_options."""

    last_kwargs: dict[str, Any] = {}

    def __init__(self, *, allowed_formats: Any = None, format_options: Any = None) -> None:
        self.allowed_formats = allowed_formats
        self.format_options = format_options
        _FakeDocumentConverter.last_kwargs = {
            "allowed_formats": allowed_formats,
            "format_options": format_options,
        }


@pytest.fixture
def docling_patched(monkeypatch: pytest.MonkeyPatch):
    """Patch document module globals so docling paths are testable without the dep."""
    _CapturePdfFormatOption.instances.clear()
    _FakeVlmConvertOpts.last_preset = None
    _FakeVlmConvertOpts.last_engine = None
    _FakeDocumentConverter.last_kwargs.clear()
    # [#audit] converter is now a process-level cache; clear it so each test
    # rebuilds (otherwise a prior test's fake converter is reused → stale kwargs).
    document_mod._DOCLING_CONVERTERS.clear()

    # VlmPipeline sentinel — pipeline_cls is captured by reference
    vlm_pipeline_cls = types.SimpleNamespace(name="VlmPipeline")

    monkeypatch.setattr(document_mod, "_DOCLING_AVAILABLE", True)
    monkeypatch.setattr(document_mod, "PdfFormatOption", _CapturePdfFormatOption)
    monkeypatch.setattr(document_mod, "ImageFormatOption", _CaptureImageFormatOption)
    monkeypatch.setattr(document_mod, "InputFormat", _FakeInputFormat)
    monkeypatch.setattr(document_mod, "DocumentConverter", _FakeDocumentConverter)
    monkeypatch.setattr(document_mod, "VlmConvertOptions", _FakeVlmConvertOpts)
    monkeypatch.setattr(document_mod, "VlmPipelineOptions", _FakeVlmPipelineOpts)
    monkeypatch.setattr(document_mod, "TransformersVlmEngineOptions", _FakeVlmEngineOpts)
    monkeypatch.setattr(document_mod, "VlmPipeline", vlm_pipeline_cls)
    return vlm_pipeline_cls


class TestVlmPipelineBuilder:
    def test_build_vlm_pipeline_calls_granite_preset(self, docling_patched):
        # Arrange
        from arrow_lake.ingest.document import DocumentParser
        parser = DocumentParser(DocumentConfig(docling_pipeline_type="vlm"))
        # Act
        opts = parser._build_docling_vlm_pipeline()
        # Assert — preset 默认 granite_docling，引擎为本地 Transformers
        assert _FakeVlmConvertOpts.last_preset == "granite_docling"
        assert isinstance(_FakeVlmConvertOpts.last_engine, _FakeVlmEngineOpts)
        assert isinstance(opts, _FakeVlmPipelineOpts)

    def test_build_vlm_pipeline_respects_custom_preset(self, docling_patched):
        # Arrange
        from arrow_lake.ingest.document import DocumentParser
        parser = DocumentParser(DocumentConfig(
            docling_pipeline_type="vlm", docling_vlm_preset="smoldocling",
        ))
        # Act
        parser._build_docling_vlm_pipeline()
        # Assert
        assert _FakeVlmConvertOpts.last_preset == "smoldocling"

    def test_vlm_converter_uses_vlm_pipeline_cls(self, docling_patched, monkeypatch):
        # Arrange — _resolve_docling_ocr 不会在 VLM 分支被调用，但 _build_docling_pipeline 引用
        monkeypatch.setattr(
            document_mod.DocumentParser, "_build_docling_pipeline", lambda self, e, l: object(),
        )
        from arrow_lake.ingest.document import DocumentParser
        parser = DocumentParser(DocumentConfig(docling_pipeline_type="vlm"))
        # Act
        converter, _convert_lock = parser._get_docling_converter()
        # Assert — PDF 的 PdfFormatOption 用了 VlmPipeline 作为 pipeline_cls
        assert isinstance(converter, _FakeDocumentConverter)
        fmt = _FakeDocumentConverter.last_kwargs["format_options"]
        pdf_opt = fmt[_FakeInputFormat.PDF]
        assert pdf_opt.pipeline_cls is docling_patched
        # VLM 分支只允许 PDF + IMAGE
        assert set(_FakeDocumentConverter.last_kwargs["allowed_formats"]) == {
            _FakeInputFormat.PDF, _FakeInputFormat.IMAGE,
        }

    def test_standard_converter_does_not_use_vlm(self, docling_patched, monkeypatch):
        # Arrange
        monkeypatch.setattr(
            document_mod.DocumentParser, "_build_docling_pipeline", lambda self, e, l: "std-pipe",
        )
        from arrow_lake.ingest.document import DocumentParser
        parser = DocumentParser(DocumentConfig(docling_pipeline_type="standard"))
        # Act
        parser._get_docling_converter()
        # Assert — 标准分支 PDF option 不带 pipeline_cls（VlmPipeline）
        fmt = _FakeDocumentConverter.last_kwargs["format_options"]
        assert fmt[_FakeInputFormat.PDF].pipeline_cls is None
        assert fmt[_FakeInputFormat.PDF].pipeline_options == "std-pipe"

    def test_docling_unavailable_raises(self, monkeypatch):
        # Arrange — 显式置 _DOCLING_AVAILABLE=False（不依赖运行时是否真装了 docling）
        monkeypatch.setattr(document_mod, "_DOCLING_AVAILABLE", False)
        from arrow_lake.ingest.document import DocumentError, DocumentParser
        parser = DocumentParser(DocumentConfig(docling_pipeline_type="vlm"))
        # Act / Assert
        with pytest.raises(DocumentError, match="docling package is not installed"):
            parser._get_docling_converter()


# ---------------------------------------------------------------------------
# HybridChunker dispatch — inject fake docling modules for lazy imports
# ---------------------------------------------------------------------------


def _install_fake_docling_chunking(
    monkeypatch: pytest.MonkeyPatch,
    captured: dict[str, Any],
    *,
    chunker_cls: Any = None,
):
    """Inject fake docling/docling_core/transformers modules so HybridChunker lazy import works."""

    if chunker_cls is None:
        class _HybridChunker:
            def __init__(self, *, tokenizer: Any = None, merge_peers: bool = True) -> None:
                captured["tokenizer"] = tokenizer
                captured["merge_peers"] = merge_peers

            def chunk(self, *, dl_doc: Any = None) -> list[Any]:
                captured["dl_doc"] = dl_doc
                # 返回 3 个可区分的假 chunk 对象
                return [MagicMock(name=f"chunk{i}") for i in range(3)]

            def contextualize(self, *, chunk: Any) -> str:
                return f"CTX-{chunk._mock_name}"
        chunker_cls = _HybridChunker

    class _HuggingFaceTokenizer:
        def __init__(self, *, tokenizer: Any) -> None:
            self.tokenizer = tokenizer

    class _AutoTokenizer:
        @staticmethod
        def from_pretrained(model_id: str) -> Any:
            return f"tok:{model_id}"

    fake_docling = types.ModuleType("docling")
    fake_docling_chunking = types.ModuleType("docling.chunking")
    fake_docling_chunking.HybridChunker = chunker_cls
    fake_docling.chunking = fake_docling_chunking

    fake_docling_core = types.ModuleType("docling_core")
    fake_ts = types.ModuleType("docling_core.transforms")
    fake_chunker = types.ModuleType("docling_core.transforms.chunker")
    fake_tok_pkg = types.ModuleType("docling_core.transforms.chunker.tokenizer")
    fake_hf_tok = types.ModuleType("docling_core.transforms.chunker.tokenizer.huggingface")
    fake_hf_tok.HuggingFaceTokenizer = _HuggingFaceTokenizer
    fake_tok_pkg.huggingface = fake_hf_tok
    fake_chunker.tokenizer = fake_tok_pkg
    fake_ts.chunker = fake_chunker
    fake_docling_core.transforms = fake_ts

    fake_transformers = types.ModuleType("transformers")
    fake_transformers.AutoTokenizer = _AutoTokenizer

    monkeypatch.setitem(sys.modules, "docling", fake_docling)
    monkeypatch.setitem(sys.modules, "docling.chunking", fake_docling_chunking)
    monkeypatch.setitem(sys.modules, "docling_core", fake_docling_core)
    monkeypatch.setitem(sys.modules, "docling_core.transforms", fake_ts)
    monkeypatch.setitem(sys.modules, "docling_core.transforms.chunker", fake_chunker)
    monkeypatch.setitem(sys.modules, "docling_core.transforms.chunker.tokenizer", fake_tok_pkg)
    monkeypatch.setitem(sys.modules, "docling_core.transforms.chunker.tokenizer.huggingface", fake_hf_tok)
    monkeypatch.setitem(sys.modules, "transformers", fake_transformers)
    # 关键：让 _validate_strategy 不把 DOCLING_HYBRID 降级
    monkeypatch.setattr(chunker_mod, "_DOCLING_CHUNK_AVAILABLE", True)


class TestHybridChunkerDispatch:
    def test_docling_hybrid_uses_hybrid_chunker(self, monkeypatch):
        # Arrange
        captured: dict[str, Any] = {}
        _install_fake_docling_chunking(monkeypatch, captured)
        chunker = DocumentChunker(strategy=ChunkStrategy.DOCLING_HYBRID)
        assert chunker._strategy == ChunkStrategy.DOCLING_HYBRID  # 未被降级
        doc = MagicMock(name="DoclingDocument")

        # Act
        chunks = chunker.chunk([(1, "ignored")], docling_doc=doc)

        # Assert — HybridChunker 被调用，dl_doc 透传，返回 Chunk 对象
        assert captured["dl_doc"] is doc
        assert len(chunks) == 3
        assert all(isinstance(c, chunker_mod.Chunk) for c in chunks)
        # 结构感知 chunk 无单一页码 → 0
        assert all(c.page_number == 0 for c in chunks)
        # chunk_index 连续
        assert [c.chunk_index for c in chunks] == [0, 1, 2]
        # 文本是 contextualize 输出
        assert chunks[0].text.startswith("CTX-")

    def test_docling_hybrid_tokenizer_aligns_with_config(self, monkeypatch):
        # Arrange
        captured: dict[str, Any] = {}
        _install_fake_docling_chunking(monkeypatch, captured)
        chunker = DocumentChunker(
            strategy=ChunkStrategy.DOCLING_HYBRID, docling_chunk_tokenizer="BAAI/bge-m3",
        )
        # Act
        chunker.chunk([(1, "x")], docling_doc=MagicMock())
        # Assert — HuggingFaceTokenizer 包了 AutoTokenizer.from_pretrained("BAAI/bge-m3")
        inner = captured["tokenizer"].tokenizer
        assert inner == "tok:BAAI/bge-m3"

    def test_docling_hybrid_degrades_when_docling_doc_missing(self, monkeypatch):
        # Arrange — docling 可用但没有 DoclingDocument（非 docling 后端）
        captured: dict[str, Any] = {}
        _install_fake_docling_chunking(monkeypatch, captured)
        chunker = DocumentChunker(strategy=ChunkStrategy.DOCLING_HYBRID, chunk_size=50)

        # Act — docling_doc=None，应降级对 pages 文本做 RECURSIVE 切分
        text = "Sentence one. Sentence two. " * 10
        chunks = chunker.chunk([(1, text)], docling_doc=None)

        # Assert — 没有调用 HybridChunker，但产出了 chunk
        assert "dl_doc" not in captured
        assert len(chunks) > 1
        assert all(isinstance(c, chunker_mod.Chunk) for c in chunks)
        # 降级路径保留页码
        assert all(c.page_number == 1 for c in chunks)

    def test_docling_hybrid_empty_doc_produces_no_chunks(self, monkeypatch):
        # Arrange — 用空返回的 HybridChunker 装载
        captured: dict[str, Any] = {}

        class _EmptyChunker:
            def __init__(self, *, tokenizer=None, merge_peers=True): pass
            def chunk(self, *, dl_doc=None): return []
            def contextualize(self, *, chunk=None): return ""

        _install_fake_docling_chunking(monkeypatch, captured, chunker_cls=_EmptyChunker)
        chunker = DocumentChunker(strategy=ChunkStrategy.DOCLING_HYBRID)

        # Act
        chunks = chunker.chunk([(1, "x")], docling_doc=MagicMock())
        # Assert — 空输入空输出
        assert chunks == []


class TestHybridChunkerValidationFallback:
    def test_docling_hybrid_falls_back_to_recursive_when_unavailable(self, monkeypatch):
        # Arrange — 真实 dev 环境 docling 未装，_DOCLING_CHUNK_AVAILABLE=False
        monkeypatch.setattr(chunker_mod, "_DOCLING_CHUNK_AVAILABLE", False)
        # Act
        chunker = DocumentChunker(strategy=ChunkStrategy.DOCLING_HYBRID)
        # Assert — 降级为 RECURSIVE
        assert chunker._strategy == ChunkStrategy.RECURSIVE
