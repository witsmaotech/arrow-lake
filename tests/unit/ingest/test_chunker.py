"""Tests for ingest/chunker.py — pure functions and DocumentChunker."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from arrow_lake.config._enums import ChunkStrategy
from arrow_lake.ingest.chunker import (
    Chunk,
    DocumentChunker,
    _split_by_paragraph,
    _split_recursive,
)


# ===========================================================================
# Chunk dataclass
# ===========================================================================


class TestChunk:
    def test_defaults(self) -> None:
        c = Chunk(text="hello")
        assert c.page_number == 0
        assert c.chunk_index == 0
        assert c.metadata is None

    def test_frozen(self) -> None:
        c = Chunk(text="hello")
        with pytest.raises(AttributeError):
            c.text = "world"  # type: ignore[misc]


# ===========================================================================
# _split_by_paragraph
# ===========================================================================


class TestSplitByParagraph:
    def test_splits_on_double_newline(self) -> None:
        result = _split_by_paragraph("para 1\n\npara 2\n\npara 3")
        assert result == ["para 1", "para 2", "para 3"]

    def test_strips_whitespace(self) -> None:
        result = _split_by_paragraph("  a  \n\n  b  ")
        assert result == ["a", "b"]

    def test_empty_string(self) -> None:
        assert _split_by_paragraph("") == []

    def test_single_paragraph(self) -> None:
        assert _split_by_paragraph("just one") == ["just one"]

    def test_extra_newlines(self) -> None:
        result = _split_by_paragraph("a\n\n\n\nb")
        assert len(result) == 2


# ===========================================================================
# _split_recursive
# ===========================================================================


class TestSplitRecursive:
    def test_short_text_returns_single(self) -> None:
        result = _split_recursive("short", size=100, overlap=0)
        assert result == ["short"]

    def test_splits_on_sentences(self) -> None:
        text = "First sentence. Second sentence. Third sentence. Fourth sentence."
        result = _split_recursive(text, size=40, overlap=0)
        assert len(result) >= 2

    def test_splits_on_words_when_no_sentences(self) -> None:
        text = "word " * 100
        result = _split_recursive(text.strip(), size=20, overlap=0)
        assert len(result) >= 2

    def test_overlap(self) -> None:
        text = "First sentence here. Second sentence here. Third sentence here."
        result = _split_recursive(text, size=30, overlap=10)
        assert len(result) >= 2

    def test_empty_text(self) -> None:
        assert _split_recursive("", size=10, overlap=0) == []

    def test_multiple_overlapping_chunks(self) -> None:
        text = "Word one. Word two. Word three. Word four. Word five. Word six."
        result = _split_recursive(text, size=20, overlap=5)
        assert len(result) >= 2


# ===========================================================================
# DocumentChunker
# ===========================================================================


class TestDocumentChunker:
    def test_default_recursive_strategy(self) -> None:
        chunker = DocumentChunker()
        pages = [(1, "Hello world. This is a test.")]
        chunks = chunker.chunk(pages)
        assert len(chunks) >= 1
        assert chunks[0].page_number == 1

    def test_paragraph_strategy(self) -> None:
        chunker = DocumentChunker(strategy=ChunkStrategy.PARAGRAPH)
        pages = [(1, "Para one.\n\nPara two.")]
        chunks = chunker.chunk(pages)
        assert len(chunks) == 2

    def test_recursive_strategy_explicit(self) -> None:
        chunker = DocumentChunker(strategy=ChunkStrategy.RECURSIVE, chunk_size=20)
        pages = [(1, "A short test.")]
        chunks = chunker.chunk(pages)
        assert len(chunks) >= 1

    def test_semchunk_fallback_without_library(self) -> None:
        chunker = DocumentChunker(strategy=ChunkStrategy.SEMCHUNK)
        # Should fallback to recursive if semchunk not available
        pages = [(1, "Test text here.")]
        chunks = chunker.chunk(pages)
        assert len(chunks) >= 1

    def test_chonkie_fallback_without_library(self) -> None:
        chunker = DocumentChunker(strategy=ChunkStrategy.CHONKIE_TOKEN)
        # Should fallback to recursive if chonkie not available
        pages = [(1, "Test text here.")]
        chunks = chunker.chunk(pages)
        assert len(chunks) >= 1

    def test_chunk_metadata(self) -> None:
        chunker = DocumentChunker()
        pages = [(2, "Some text.")]
        chunks = chunker.chunk(pages)
        assert chunks[0].page_number == 2
        assert chunks[0].chunk_index == 0

    def test_empty_pages(self) -> None:
        chunker = DocumentChunker()
        chunks = chunker.chunk([])
        assert chunks == []

    def test_multiple_pages(self) -> None:
        chunker = DocumentChunker()
        pages = [(1, "Page one text."), (2, "Page two text.")]
        chunks = chunker.chunk(pages)
        assert any(c.page_number == 1 for c in chunks)
        assert any(c.page_number == 2 for c in chunks)
