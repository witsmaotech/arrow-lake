"""Tests for /ingest/documents writing the canonical text_content column (v1.8.9).

E2E found documents ingested via ``/ingest/documents`` were invisible to FTS
retrieval: the path wrote column ``text`` while the whole retrieval stack
defaults to ``text_content``. Fixed by writing ``text_content``. (Embedding is
triggered at the router/Lake layer via ``embed_and_add`` after ingest — verified
live, not in this mixin unit test.)
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
    m._build_report = lambda sources: SimpleNamespace(sources=sources)
    return m


def test_ingest_documents_writes_text_content_column(
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
