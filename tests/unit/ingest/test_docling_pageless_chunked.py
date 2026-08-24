"""分块 convert 对无页格式(md/html 等)的回归测试。

2026-08-24 实证:`docling_chunk_size=50`(部署默认)下摄入 .md 报
"Docling returned empty content" —— md 走 SimplePipeline **没有页概念**,
`page_range=(1,50)` 切片返回的 doc 页数为 0,分块循环把首块当"越过文档
末尾"丢弃 → docs=[] → 误报空。修:首块 0 页但有内容(texts/tables)时
整篇收下(拆页兜底在 _build_parsed_from_docling 的 not pages 分支)。
"""

from __future__ import annotations

import threading
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from arrow_lake.ingest.document import DocumentParser


def _parser() -> DocumentParser:
    return DocumentParser.__new__(DocumentParser)  # 绕过 __init__(不需要配置)


def _pageless_doc(text: str = "正文内容") -> SimpleNamespace:
    """md 后端产物:texts 有内容但 num_pages()=0(无页格式)。"""
    doc = SimpleNamespace(
        texts=[SimpleNamespace(text=text, prov=[])],
        tables=[],
        num_pages=lambda: 0,
        export_to_markdown=lambda: text,
    )
    return doc


def _pageful_doc(n_pages: int) -> SimpleNamespace:
    return SimpleNamespace(texts=[], tables=[], num_pages=lambda: n_pages,
                           export_to_markdown=lambda: "pdf")


def _converter(docs_by_call: list) -> MagicMock:
    """按调用次序返回给定 doc 的转换器桩(convert 忽略 page_range)。"""
    conv = MagicMock()
    calls = iter(docs_by_call)
    def _convert(path, page_range=None):  # noqa: ANN001, ANN202
        return SimpleNamespace(document=next(calls), confidence=None)
    conv.convert = _convert
    return conv


class TestPagelessFormats:
    def test_md_first_chunk_zero_pages_with_content_is_kept(self) -> None:
        parser = _parser()
        conv = _converter([_pageless_doc("## 标题\n\n正文")])
        docs, conf = parser._convert_docling_chunked(
            conv, threading.RLock(), __import__("pathlib").Path("/t.md"),
            chunk_size=50, max_pages=0,
        )
        assert len(docs) == 1
        assert "正文" in docs[0].export_to_markdown()

    def test_second_slice_zero_pages_stops_without_extra_doc(self) -> None:
        """有页格式跨块:第二块 0 页 = 越过末尾,只收第一块。"""
        parser = _parser()
        conv = _converter([_pageful_doc(50), _pageless_doc("tail")])
        docs, _ = parser._convert_docling_chunked(
            conv, threading.RLock(), __import__("pathlib").Path("/t.pdf"),
            chunk_size=50, max_pages=0,
        )
        assert len(docs) == 1

    def test_truly_empty_pageless_doc_still_yields_empty(self) -> None:
        """真·空文档(0 页也无内容)保持旧行为 → 上游报 empty content。"""
        parser = _parser()
        empty = SimpleNamespace(texts=[], tables=[], num_pages=lambda: 0,
                                export_to_markdown=lambda: "")
        conv = _converter([empty])
        docs, _ = parser._convert_docling_chunked(
            conv, threading.RLock(), __import__("pathlib").Path("/t.md"),
            chunk_size=50, max_pages=0,
        )
        assert docs == []
