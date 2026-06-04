"""Targeted tests for rag/context.py — uncovered paths."""

from __future__ import annotations

import pyarrow as pa
import pytest

from arrow_lake.rag.context import (
    ContextChunk,
    ContextWindow,
    count_tokens,
    table_to_chunks,
)


class TestCountTokens:
    def test_cjk_text(self) -> None:
        # Exercise CJK heuristic path
        result = count_tokens("你好世界这是一个测试")
        assert result > 0

    def test_short_ascii(self) -> None:
        result = count_tokens("hello world")
        assert result > 0


class TestContextWindow:
    def test_add_chunk_and_dedup(self) -> None:
        ctx = ContextWindow(token_budget=1000)
        chunk1 = ContextChunk(text="hello", dataset="ds", row_id="r1", score=0.9)
        chunk2 = ContextChunk(text="world", dataset="ds", row_id="r1", score=0.8)
        assert ctx.add_chunk(chunk1) is True
        assert ctx.add_chunk(chunk2) is False  # duplicate
        assert ctx.chunk_count == 1

    def test_skip_dedup(self) -> None:
        ctx = ContextWindow(token_budget=1000)
        chunk1 = ContextChunk(text="hello", dataset="ds", row_id="r1", score=0.9)
        chunk2 = ContextChunk(text="world", dataset="ds", row_id="r1", score=0.8)
        ctx.add_chunk(chunk1)
        assert ctx.add_chunk(chunk2, skip_dedup=True) is True
        assert ctx.chunk_count == 2

    def test_finalize_applies_budget(self) -> None:
        ctx = ContextWindow(token_budget=5)
        ctx.add_chunk(ContextChunk(text="hello world test", dataset="ds", row_id="r1", score=0.9))
        ctx.add_chunk(ContextChunk(text="another text here", dataset="ds", row_id="r2", score=0.5))
        ctx.finalize()
        assert ctx.chunk_count >= 1

    def test_citations(self) -> None:
        ctx = ContextWindow(token_budget=1000)
        ctx.add_chunk(ContextChunk(text="hello", dataset="ds", row_id="r1", score=0.9))
        ctx.finalize()
        assert len(ctx.citations) == 1


class TestTableToChunks:
    def test_with_metadata(self) -> None:
        table = pa.table({
            "text_content": ["hello", "world"],
            "row_id": ["r1", "r2"],
            "score": [0.9, 0.8],
            "meta": [{"source": "a"}, {"source": "b"}],
        })
        chunks = table_to_chunks(table, "ds", score_column="score", metadata_column="meta")
        assert len(chunks) == 2
        assert chunks[0].metadata == {"source": "a"}
        assert chunks[0].score == 0.9

    def test_with_null_text(self) -> None:
        table = pa.table({
            "text_content": [None, "hello"],
            "row_id": ["r1", "r2"],
        })
        chunks = table_to_chunks(table, "ds")
        assert chunks[0].text == ""
        assert chunks[1].text == "hello"

    def test_without_score_column(self) -> None:
        table = pa.table({
            "text_content": ["a"],
            "row_id": ["r1"],
        })
        chunks = table_to_chunks(table, "ds")
        assert chunks[0].score == 1.0
