"""Tests for RAG context assembly and token management — M2 Day 3."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from arrow_lake.rag.context import ContextChunk, ContextWindow, table_to_chunks

# ---------------------------------------------------------------------------
# ContextChunk
# ---------------------------------------------------------------------------


class TestContextChunk:
    def test_construction(self) -> None:
        chunk = ContextChunk(
            text="Sample text",
            dataset="documents",
            row_id="doc-1",
            score=0.95,
            metadata={"page": 3},
        )
        assert chunk.text == "Sample text"
        assert chunk.dataset == "documents"
        assert chunk.row_id == "doc-1"
        assert chunk.score == 0.95
        assert chunk.metadata == {"page": 3}

    def test_frozen(self) -> None:
        chunk = ContextChunk(text="t", dataset="d", row_id="r", score=1.0)
        with pytest.raises(AttributeError):
            chunk.text = "modified"  # type: ignore[misc]

    def test_optional_metadata(self) -> None:
        chunk = ContextChunk(text="t", dataset="d", row_id="r", score=0.5)
        assert chunk.metadata is None


# ---------------------------------------------------------------------------
# ContextWindow
# ---------------------------------------------------------------------------


class TestContextWindow:
    def test_empty_window(self) -> None:
        window = ContextWindow(token_budget=100)
        assert window.token_count == 0
        assert window.chunk_count == 0
        assert window.assemble() == ""

    def test_add_chunk_within_budget(self) -> None:
        window = ContextWindow(token_budget=100)
        chunk = ContextChunk(
            text="Hello world", dataset="docs", row_id="1", score=0.9
        )
        result = window.add_chunk(chunk)
        assert result is True
        window.finalize()
        assert window.chunk_count == 1

    def test_add_chunk_exceeds_budget(self) -> None:
        with patch("arrow_lake.rag.context.count_tokens") as mock_count:
            mock_count.side_effect = lambda t: len(t) // 4
            window = ContextWindow(token_budget=5)
            chunk = ContextChunk(
                text="This is a very long text that exceeds budget",
                dataset="docs", row_id="1", score=0.9,
            )
            result = window.add_chunk(chunk)
            assert result is True
            window.finalize()
            assert window.chunk_count == 1
            assert window.token_count <= 5

    def test_add_chunk_zero_budget(self) -> None:
        window = ContextWindow(token_budget=0)
        chunk = ContextChunk(
            text="Any text at all", dataset="docs", row_id="1", score=0.9
        )
        result = window.add_chunk(chunk)
        assert result is True  # add_chunk collects all; finalize applies budget
        window.finalize()
        assert window.chunk_count == 0

    def test_budget_truncation(self) -> None:
        window = ContextWindow(token_budget=10)
        chunk = ContextChunk(
            text="Hello world this is extra text", dataset="docs", row_id="1", score=0.9
        )
        window.add_chunk(chunk)
        window.finalize()
        assert window.token_count <= 10
        assert window.chunk_count == 1

    def test_dedup_by_dataset_row_id(self) -> None:
        window = ContextWindow(token_budget=200)
        chunk1 = ContextChunk(text="First", dataset="docs", row_id="1", score=0.9)
        chunk2 = ContextChunk(text="Second", dataset="docs", row_id="1", score=0.8)
        chunk3 = ContextChunk(text="Third", dataset="docs", row_id="2", score=0.7)

        window.add_chunk(chunk1)
        window.add_chunk(chunk2)  # duplicate — same dataset+row_id
        window.add_chunk(chunk3)

        assert window.chunk_count == 2  # dedup still works in add_chunk

    def test_assemble_with_citations(self) -> None:
        window = ContextWindow(token_budget=200)
        window.add_chunk(ContextChunk(text="Alpha content", dataset="docs", row_id="1", score=0.9))
        window.add_chunk(ContextChunk(text="Beta content", dataset="docs", row_id="2", score=0.8))

        assembled = window.assemble()
        assert "Alpha content" in assembled
        assert "Beta content" in assembled

    def test_assemble_score_ordering_after_finalize(self) -> None:
        window = ContextWindow(token_budget=200)
        window.add_chunk(ContextChunk(text="Low score", dataset="a", row_id="1", score=0.3))
        window.add_chunk(ContextChunk(text="High score", dataset="a", row_id="2", score=0.9))
        window.finalize()

        assembled = window.assemble()
        pos_high = assembled.index("High score")
        pos_low = assembled.index("Low score")
        assert pos_high < pos_low  # Higher score comes first after finalize

    def test_citations_list(self) -> None:
        window = ContextWindow(token_budget=200)
        window.add_chunk(ContextChunk(text="Text A", dataset="ds1", row_id="r1", score=0.9, metadata={"title": "Doc A"}))
        window.add_chunk(ContextChunk(text="Text B", dataset="ds2", row_id="r2", score=0.8))

        citations = window.citations
        assert len(citations) == 2
        assert citations[0].dataset == "ds1"
        assert citations[0].row_id == "r1"
        assert citations[1].dataset == "ds2"

    def test_clear(self) -> None:
        window = ContextWindow(token_budget=100)
        window.add_chunk(ContextChunk(text="X", dataset="d", row_id="r", score=0.5))
        assert window.chunk_count == 1
        window.clear()
        assert window.chunk_count == 0
        assert window.token_count == 0

    def test_max_chunks_limit(self) -> None:
        window = ContextWindow(token_budget=10000, max_chunks=2)
        for i in range(5):
            window.add_chunk(ContextChunk(text=f"Chunk {i}", dataset="d", row_id=str(i), score=float(i)))
        window.finalize()
        assert window.chunk_count == 2


# ---------------------------------------------------------------------------
# count_tokens
# ---------------------------------------------------------------------------


class TestCountTokens:
    def test_count_with_tiktoken(self) -> None:
        with patch("arrow_lake.rag.context._has_tiktoken", True):
            mock_tiktoken = MagicMock()
            mock_enc = MagicMock()
            mock_enc.encode.return_value = [1, 2, 3, 4, 5]
            mock_tiktoken.encoding_for_model.return_value = mock_enc

            with patch.dict("sys.modules", {"tiktoken": mock_tiktoken}):
                # Re-import to pick up patched module
                import importlib

                import arrow_lake.rag.context as ctx_mod
                importlib.reload(ctx_mod)
                count = ctx_mod.count_tokens("Hello world")

        assert count == 5

    def test_count_fallback_heuristic(self) -> None:
        with patch("arrow_lake.rag.context._has_tiktoken", False):
            import importlib

            import arrow_lake.rag.context as ctx_mod
            importlib.reload(ctx_mod)
            count = ctx_mod.count_tokens("Hello world")

        # heuristic: len(text) // 4
        assert count == 2  # "Hello world" is 11 chars, 11//4 = 2


# ---------------------------------------------------------------------------
# table_to_chunks
# ---------------------------------------------------------------------------


class TestTableToChunks:
    def test_pyarrow_table_to_chunks(self) -> None:
        import pyarrow as pa

        table = pa.table({
            "text_content": ["First document", "Second document"],
            "row_id": ["doc-1", "doc-2"],
            "_score": [0.95, 0.85],
        })

        chunks = table_to_chunks(table, dataset_name="my_docs", score_column="_score")
        assert len(chunks) == 2
        assert chunks[0].text == "First document"
        assert chunks[0].row_id == "doc-1"
        assert chunks[0].score == 0.95
        assert chunks[0].dataset == "my_docs"
        assert chunks[0].metadata is None
        assert chunks[1].metadata is None

    def test_no_score_column(self) -> None:
        import pyarrow as pa

        table = pa.table({
            "text_content": ["Doc A"],
            "row_id": ["a"],
        })

        chunks = table_to_chunks(table, dataset_name="docs")
        assert len(chunks) == 1
        assert chunks[0].score == 1.0  # default when no score column

    def test_empty_table(self) -> None:
        import pyarrow as pa

        table = pa.table({
            "text_content": pa.array([], type=pa.string()),
            "row_id": pa.array([], type=pa.string()),
        })

        chunks = table_to_chunks(table, dataset_name="empty")
        assert len(chunks) == 0

    def test_missing_row_id_fallback(self) -> None:
        """When row_id column is absent, fallback to range index."""
        import pyarrow as pa

        table = pa.table({
            "text_content": ["Doc A", "Doc B", "Doc C"],
            "_score": [0.9, 0.8, 0.7],
        })

        chunks = table_to_chunks(table, dataset_name="fts_results", score_column="_score")
        assert len(chunks) == 3
        assert chunks[0].row_id == "0"
        assert chunks[1].row_id == "1"
        assert chunks[2].row_id == "2"
