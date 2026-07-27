"""M2 backward compatibility regression tests.

Ensures M3 knowledge graph changes do not break existing RAG functionality.
"""

from __future__ import annotations

import inspect
from dataclasses import fields


class TestRAGPipelineSignature:
    """Verify RAGPipeline.query() signature is stable."""

    def test_query_method_signature(self) -> None:
        """RAGPipeline.query must accept (question, dataset_name, *, top_k, strategy, template_name, session_id, use_kg)."""
        from arrow_lake.rag.pipeline import RAGPipeline

        sig = inspect.signature(RAGPipeline.query)
        params = list(sig.parameters.keys())
        assert params == ["self", "question", "dataset_name", "top_k", "strategy", "template_name", "session_id", "use_kg"]

    def test_extract_entities_signature(self) -> None:
        """RAGPipeline.extract_entities must accept (dataset_name, *, text_column, top_k, template_name)."""
        from arrow_lake.rag.pipeline import RAGPipeline

        sig = inspect.signature(RAGPipeline.extract_entities)
        params = list(sig.parameters.keys())
        assert params == ["self", "dataset_name", "text_column", "top_k", "template_name"]

    def test_query_stream_signature(self) -> None:
        """RAGPipeline.query_stream must accept (question, dataset_name, *, top_k, strategy, template_name)."""
        from arrow_lake.rag.pipeline import RAGPipeline

        sig = inspect.signature(RAGPipeline.query_stream)
        params = list(sig.parameters.keys())
        assert params == ["self", "question", "dataset_name", "top_k", "strategy", "template_name"]


class TestRAGResponseFields:
    """Verify RAGResponse dataclass fields are stable."""

    def test_rag_response_fields(self) -> None:
        """RAGResponse must have: answer, citations, retrieval_count, context_tokens, llm_usage, latency_ms, session_id."""
        from arrow_lake.rag.pipeline import RAGResponse

        field_names = {f.name for f in fields(RAGResponse)}
        expected = {"answer", "citations", "retrieval_count", "context_tokens", "llm_usage", "latency_ms", "session_id"}
        assert expected.issubset(field_names), f"Missing fields: {expected - field_names}"

    def test_rag_response_is_frozen(self) -> None:
        """RAGResponse must be a frozen dataclass."""
        from arrow_lake.rag.pipeline import RAGResponse

        assert getattr(RAGResponse, "__dataclass_params__", None) is not None
        assert RAGResponse.__dataclass_params__.frozen is True


class TestContextWindowAssembly:
    """Verify ContextWindow.assemble() still works for plain text."""

    def test_assemble_plain_text(self) -> None:
        """ContextWindow.assemble() with no graph chunks produces numbered format."""
        from arrow_lake.rag.context import ContextChunk, ContextWindow

        window = ContextWindow(token_budget=4096)
        ok = window.add_chunk(ContextChunk(
            text="First document content.",
            dataset="documents",
            row_id="doc-1",
            score=0.95,
        ))
        assert ok is True

        result = window.assemble()
        assert result == "[1] First document content."

    def test_assemble_empty_window(self) -> None:
        """ContextWindow.assemble() on empty window returns empty string."""
        from arrow_lake.rag.context import ContextWindow

        window = ContextWindow(token_budget=4096)
        assert window.assemble() == ""

    def test_add_graph_context(self) -> None:
        """ContextWindow.add_graph_context() adds a synthetic graph chunk."""
        from arrow_lake.rag.context import ContextWindow

        window = ContextWindow(token_budget=4096)
        ok = window.add_graph_context("entity1 -- related_to --> entity2")
        assert ok is True
        assert window.chunk_count == 1

        result = window.assemble()
        assert "Knowledge Graph Context" in result
        assert "entity1" in result


class TestPromptRegistryDefaults:
    """Verify PromptRegistry still contains expected built-in templates."""

    def test_default_templates_exist(self) -> None:
        """PromptRegistry must contain default_qa, entity_extract, summarize."""
        from arrow_lake.rag.prompt import PromptRegistry

        registry = PromptRegistry()
        names = registry.list_templates()

        assert "default_qa" in names
        assert "entity_extract" in names
        assert "summarize" in names

    def test_graph_templates_exist(self) -> None:
        """PromptRegistry must contain graph_qa (added in M3)."""
        from arrow_lake.rag.prompt import PromptRegistry

        registry = PromptRegistry()
        names = registry.list_templates()

        assert "graph_qa" in names
        assert "entity_extract_from_question" in names

    def test_template_rendering(self) -> None:
        """Each template must render without error when given valid variables."""
        from arrow_lake.rag.prompt import PromptRegistry

        registry = PromptRegistry()
        qa = registry.get("default_qa")
        assert qa is not None
        result = qa.render(context="Some context", question="What?")
        assert "Some context" in result
        assert "What?" in result

        extract = registry.get("entity_extract")
        assert extract is not None
        result = extract.render(text="Some text")
        assert "Some text" in result
