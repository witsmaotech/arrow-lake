"""Tests for /ingest/documents writing the canonical schema + auto-embed (v1.8.9).

E2E found documents ingested via ``/ingest/documents`` were invisible to
retrieval: the path wrote column ``text`` (FTS wants ``text_content``) and never
embedded (vector wants ``text_embedding``). Fixed by writing ``text_content``
and calling ``embed_and_add`` after the write.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from arrow_lake.ingest._ingest_files import _FileIngestMixin


def _make_mixin() -> _FileIngestMixin:
    m = object.__new__(_FileIngestMixin)
    m._doc_type_classifier = None
    m._write_table = MagicMock()
    m.embed_and_add = MagicMock(return_value=1)
    m._build_report = lambda sources: SimpleNamespace(sources=sources)
    return m


def test_ingest_documents_writes_text_content_and_calls_embed(
    monkeypatch: pytest.MonkeyPatch, tmp_path,
) -> None:
    fake_doc = SimpleNamespace(pages=[(1, "hello world")], text="hello world")
    fake_parser = MagicMock()
    fake_parser.parse.return_value = fake_doc
    monkeypatch.setattr("arrow_lake.ingest.document.DocumentParser", lambda *a, **k: fake_parser)

    fake_chunk = SimpleNamespace(text="hello world", page_number=1, chunk_index=0)
    fake_chunker = MagicMock()
    fake_chunker.chunk.return_value = [fake_chunk]
    monkeypatch.setattr("arrow_lake.ingest.chunker.DocumentChunker", lambda *a, **k: fake_chunker)

    f = tmp_path / "x.txt"
    f.write_text("hello world")

    m = _make_mixin()
    m.ingest_documents("ds", [str(f)])

    table = m._write_table.call_args.args[1]
    assert "text_content" in table.column_names   # canonical column (FTS-compatible)
    assert "text" not in table.column_names        # old non-standard name gone
    m.embed_and_add.assert_called_once_with("ds")  # auto-embed after write


def test_ingest_documents_embed_failure_does_not_block_ingest(
    monkeypatch: pytest.MonkeyPatch, tmp_path,
) -> None:
    fake_doc = SimpleNamespace(pages=[(1, "hello world")], text="hello world")
    fake_parser = MagicMock()
    fake_parser.parse.return_value = fake_doc
    monkeypatch.setattr("arrow_lake.ingest.document.DocumentParser", lambda *a, **k: fake_parser)

    fake_chunker = MagicMock()
    fake_chunker.chunk.return_value = [SimpleNamespace(text="x", page_number=1, chunk_index=0)]
    monkeypatch.setattr("arrow_lake.ingest.chunker.DocumentChunker", lambda *a, **k: fake_chunker)

    f = tmp_path / "y.txt"
    f.write_text("hello world")

    m = _make_mixin()
    m.embed_and_add.side_effect = RuntimeError("embedder down")  # best-effort path
    report = m.ingest_documents("ds", [str(f)])  # must NOT raise
    assert m._write_table.called           # text_content still written
    assert report is not None              # ingest succeeded despite embed failure
