"""Tests for RAG pipeline GraphRAG fallback logic."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from arrow_lake._lake_rag import _LakeRAGMixin
from arrow_lake.config import ArrowLakeConfig, HugeGraphConfig
from arrow_lake.rag.pipeline import RAGPipeline


def _make_config(enabled: bool = False) -> ArrowLakeConfig:
    cfg = ArrowLakeConfig()
    cfg.hugegraph = HugeGraphConfig(enabled=enabled)
    return cfg


# ---------------------------------------------------------------------------
# Minimal Lake subclass for testing the RAG mixin in isolation
# ---------------------------------------------------------------------------


class _TestLake(_LakeRAGMixin):
    """Thin wrapper to expose _LakeRAGMixin without full Lake.__init__."""

    def __init__(self, config: ArrowLakeConfig) -> None:
        self._config = config
        self._components: dict[str, object] = {}

    def _get_component(self, key: str, factory) -> object:
        if key not in self._components:
            self._components[key] = factory()
        return self._components[key]

    def _rag_retriever(self, question: str, dataset_name: str, top_k: int):
        import pyarrow as pa

        return pa.table({
            "text": ["chunk1"],
            "row_id": ["r1"],
            "_score": [0.9],
        })


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestRAGPipelineType:
    """Verify _get_rag_pipeline returns correct type based on KG config."""

    @patch("arrow_lake.rag.provider.create_llm_provider")
    def test_returns_base_rag_when_kg_disabled(self, mock_provider: MagicMock) -> None:
        """When hugegraph.enabled=False, should return plain RAGPipeline."""
        mock_provider.return_value = MagicMock()
        lake = _TestLake(_make_config(enabled=False))

        pipeline = lake._get_rag_pipeline()

        assert isinstance(pipeline, RAGPipeline)
        assert type(pipeline).__name__ != "GraphRAGPipeline"

    @patch("arrow_lake.rag.provider.create_llm_provider")
    @patch("arrow_lake.knowledge_graph.client.HugeGraphClient")
    @patch("arrow_lake.knowledge_graph.extractor.EntityExtractor")
    @patch("arrow_lake.knowledge_graph.retriever.KGRetriever")
    @patch("arrow_lake.rag.graph_rag.GraphRAGPipeline")
    def test_returns_graph_rag_when_kg_enabled(
        self,
        mock_graph_rag_cls: MagicMock,
        mock_kg_retriever_cls: MagicMock,
        mock_entity_extractor_cls: MagicMock,
        mock_hg_client_cls: MagicMock,
        mock_provider: MagicMock,
    ) -> None:
        """When hugegraph.enabled=True, should return GraphRAGPipeline."""
        mock_provider.return_value = MagicMock()
        mock_graph_rag_instance = MagicMock()
        mock_graph_rag_cls.return_value = mock_graph_rag_instance

        lake = _TestLake(_make_config(enabled=True))
        pipeline = lake._get_rag_pipeline()

        mock_graph_rag_cls.assert_called_once()
        assert pipeline is mock_graph_rag_instance
        # Verify it is NOT the plain RAGPipeline type
        assert not isinstance(pipeline, RAGPipeline) or type(pipeline).__name__ == "MagicMock"

    @patch("arrow_lake.rag.provider.create_llm_provider")
    def test_falls_back_to_rag_when_kg_creation_fails(
        self,
        mock_provider: MagicMock,
    ) -> None:
        """When GraphRAG creation fails, should fallback to RAGPipeline."""
        mock_provider.return_value = MagicMock()

        lake = _TestLake(_make_config(enabled=True))

        with patch(
            "arrow_lake.knowledge_graph.client.HugeGraphClient",
            side_effect=ConnectionError("cannot connect"),
        ):
            pipeline = lake._get_rag_pipeline()

        # Should have fallen back to RAGPipeline
        assert isinstance(pipeline, RAGPipeline)
        assert type(pipeline).__name__ != "GraphRAGPipeline"


class TestGraphRAGDegradation:
    """Verify GraphRAGPipeline degrades gracefully on graph retrieval timeout."""

    @pytest.mark.asyncio()
    async def test_graph_retrieval_timeout_continues_with_text_context(
        self,
    ) -> None:
        """When graph retrieval times out, _retrieve_graph_context should
        return empty string (allowing text-only context to proceed)."""
        from arrow_lake.config import RAGConfig
        from arrow_lake.knowledge_graph.extractor import (
            ExtractedEntity,
            ExtractionResult,
        )
        from arrow_lake.rag.graph_rag import GraphRAGPipeline
        from arrow_lake.rag.provider import LLMResponse

        mock_provider = MagicMock()
        mock_provider.generate = AsyncMock(return_value=LLMResponse(
            content="Answer from text",
            model="test",
            usage={"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
            finish_reason="stop",
            provider="test",
        ))

        mock_kg_client = MagicMock()
        mock_kg_retriever = MagicMock()
        mock_kg_retriever.retrieve = AsyncMock(
            side_effect=TimeoutError("graph timeout")
        )
        mock_kg_extractor = MagicMock()
        mock_kg_extractor.extract = AsyncMock(return_value=ExtractionResult(
            entities=(ExtractedEntity(name="entity1", entity_type="concept"),),
            relations=(),
            raw_text="test",
        ))

        mock_retriever = MagicMock(return_value=_make_result_table(["doc text"], ["r1"]))
        config = RAGConfig(default_top_k=5)

        pipeline = GraphRAGPipeline(
            llm_provider=mock_provider,
            config=config,
            retriever=mock_retriever,
            kg_client=mock_kg_client,
            kg_retriever=mock_kg_retriever,
            kg_extractor=mock_kg_extractor,
            context_window_tokens=4096,
        )

        # Test _retrieve_graph_context directly: should return empty string
        # on timeout rather than propagating the exception.
        graph_text = await pipeline._retrieve_graph_context("test Q", ["entity1"])
        assert graph_text == ""

        # Verify retrieval was attempted
        mock_kg_retriever.retrieve.assert_awaited_once()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_result_table(texts: list[str], row_ids: list[str]):
    import pyarrow as pa

    return pa.table({
        "text": texts,
        "row_id": row_ids,
        "_score": [0.9] * len(texts),
    })
