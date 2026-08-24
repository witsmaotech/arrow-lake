"""docling 格式全集接入(2026-08-24,E2E 验证时发现的口径不一致):

* API 契约(``_DOCUMENT_EXTENSIONS``)声称收 md/docx/html/... ,而 docling 转换
  器此前 VLM 分支只允许 ``[PDF, IMAGE]``、标准分支手工枚举 —— 版本升级/格式
  增补即漂移(md 实测被挡)。改为 ``list(InputFormat)`` 全集,docling 能识别
  什么就收什么;
* docling 无对应格式的扩展(.rtf/.rst/.org)回落 kreuzberg,不再报
  empty content / convert 失败;
* 审计钉住:两个分支都不再手写格式枚举、parse 分发走 _docling_handles。
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
SRC = (ROOT / "arrow_lake/ingest/document.py").read_text(encoding="utf-8")


def test_docling_handles_with_format_table(monkeypatch) -> None:
    from arrow_lake.ingest import document as doc_mod

    monkeypatch.setattr(
        doc_mod, "FormatToExtensions",
        {"md": ["md", "markdown"], "pdf": ["pdf"]},
        raising=False,
    )
    assert doc_mod._docling_handles(Path("/a/b.md")) is True
    assert doc_mod._docling_handles(Path("/a/b.MARKDOWN")) is True  # 大小写不敏感
    assert doc_mod._docling_handles(Path("/a/b.pdf")) is True
    assert doc_mod._docling_handles(Path("/a/b.rtf")) is False


def test_docling_handles_without_docling(monkeypatch) -> None:
    """docling 未装(host 测试环境):一律 False → 全部走非 docling 后端。"""
    from arrow_lake.ingest import document as doc_mod

    monkeypatch.setattr(doc_mod, "FormatToExtensions", None, raising=False)
    assert doc_mod._docling_handles(Path("/a/b.pdf")) is False


def test_allowed_formats_is_full_universe(monkeypatch) -> None:
    """allowed = list(InputFormat) 全集,非手工枚举。"""
    from arrow_lake.ingest import document as doc_mod

    class _Fmt:
        MD = "md"
        PDF = "pdf"
        EPUB = "epub"
        LATEX = "latex"

    monkeypatch.setattr(doc_mod, "InputFormat", _Fmt, raising=False)
    allowed = doc_mod._docling_allowed_formats()
    assert set(allowed) == {"md", "pdf", "epub", "latex"}


# --- 接线审计(host 无法 import docling,用源码钉)---------------------------


def test_no_hardcoded_format_whitelist_in_converter_branches() -> None:
    """VLM/标准两个分支都不允许再手写 allowed_formats 枚举。"""
    assert "allowed_formats=_docling_allowed_formats()" in SRC, (
        "两个 _build_docling_converter 分支必须用全集 helper"
    )
    assert "allowed_formats=[InputFormat.PDF" not in SRC, (
        "VLM 分支遗留硬编码 [PDF, IMAGE] 白名单"
    )
    assert SRC.count("allowed_formats=_docling_allowed_formats()") == 2, (
        "VLM + 标准两处都要接全集"
    )


def test_parse_dispatch_falls_back_for_non_docling_extensions() -> None:
    """docling 后端下,.rtf/.rst 等无 docling 格式的文件回落 kreuzberg。"""
    i = SRC.index("elif self._config.ocr_backend == OcrBackend.DOCLING:")
    block = SRC[i : i + 420]
    assert "_docling_handles(file_path)" in block, "分发须按扩展名判 docling 可达"
    assert "_parse_kreuzberg" in block, "不可达扩展须回落 kreuzberg"
