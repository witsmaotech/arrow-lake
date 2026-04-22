"""Tests for enhanced ContextWindow (graph context support) -- M3 Week 3 Day 1-2."""

from __future__ import annotations

from arrow_lake.rag.context import ContextChunk, ContextWindow


class TestAddGraphContext:
    def test_add_graph_context_success(self) -> None:
        window = ContextWindow(token_budget=200)
        result = window.add_graph_context("A --knows--> B")
        assert result is True
        assert window.chunk_count == 1

    def test_add_graph_context_empty(self) -> None:
        window = ContextWindow(token_budget=200)
        assert window.add_graph_context("") is False
        assert window.add_graph_context("   ") is False
        assert window.add_graph_context(None) is False  # type: ignore[arg-type]
        assert window.chunk_count == 0

    def test_add_graph_context_with_existing_text_chunks(self) -> None:
        window = ContextWindow(token_budget=500)
        window.add_chunk(ContextChunk(text="Doc text", dataset="docs", row_id="r1", score=0.9))
        window.add_graph_context("X --rel--> Y")
        assert window.chunk_count == 2


class TestAssembleWithGraph:
    def test_assemble_with_graph(self) -> None:
        window = ContextWindow(token_budget=500)
        window.add_graph_context("A --knows--> B")
        window.add_chunk(ContextChunk(text="Doc text", dataset="docs", row_id="r1", score=0.9))

        assembled = window.assemble()
        assert "== Knowledge Graph Context ==" in assembled
        assert "== Document Context ==" in assembled
        assert "A --knows--> B" in assembled
        assert "Doc text" in assembled

    def test_assemble_graph_before_text(self) -> None:
        window = ContextWindow(token_budget=500)
        window.add_chunk(ContextChunk(text="Doc text", dataset="docs", row_id="r1", score=0.9))
        window.add_graph_context("A --knows--> B")

        assembled = window.assemble()
        kg_pos = assembled.index("== Knowledge Graph Context ==")
        doc_pos = assembled.index("== Document Context ==")
        assert kg_pos < doc_pos

    def test_assemble_without_graph(self) -> None:
        window = ContextWindow(token_budget=500)
        window.add_chunk(ContextChunk(text="Doc text", dataset="docs", row_id="r1", score=0.9))

        assembled = window.assemble()
        # When no graph chunks are present, original numbered format is used
        assert "== Knowledge Graph Context ==" not in assembled
        assert "== Document Context ==" not in assembled
        assert "[1] Doc text" in assembled

    def test_assemble_only_graph(self) -> None:
        window = ContextWindow(token_budget=500)
        window.add_graph_context("A --knows--> B")

        assembled = window.assemble()
        assert "== Knowledge Graph Context ==" in assembled
        assert "== Document Context ==" not in assembled
