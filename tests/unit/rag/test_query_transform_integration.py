"""Tests for S2.2 query transform pipeline integration."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from arrow_lake.config import RAGConfig
from arrow_lake.rag.pipeline import RAGPipeline
from arrow_lake.rag.query_transform import (
    BaseQueryTransformer,
    HyDETransformer,
    IdentityTransformer,
    MultiQueryTransformer,
    create_query_transformer,
)


# ── Factory tests ──


class TestCreateQueryTransformer:
    def test_none_returns_identity(self) -> None:
        t = create_query_transformer("none")
        assert isinstance(t, IdentityTransformer)

    def test_empty_returns_identity(self) -> None:
        t = create_query_transformer("")
        assert isinstance(t, IdentityTransformer)

    def test_identity_returns_identity(self) -> None:
        t = create_query_transformer("identity")
        assert isinstance(t, IdentityTransformer)

    def test_hyde_without_provider_falls_back(self) -> None:
        t = create_query_transformer("hyde")
        assert isinstance(t, IdentityTransformer)

    def test_hyde_with_provider(self) -> None:
        provider = MagicMock()
        t = create_query_transformer("hyde", provider=provider, hyde_max_tokens=512)
        assert isinstance(t, HyDETransformer)
        assert t._max_answer_tokens == 512

    def test_multi_query_without_provider_falls_back(self) -> None:
        t = create_query_transformer("multi_query")
        assert isinstance(t, IdentityTransformer)

    def test_multi_query_with_provider(self) -> None:
        provider = MagicMock()
        t = create_query_transformer("multi_query", provider=provider, multi_query_variants=5)
        assert isinstance(t, MultiQueryTransformer)
        assert t._num_variants == 5

    def test_unknown_kind_falls_back(self) -> None:
        t = create_query_transformer("unknown_xyz")
        assert isinstance(t, IdentityTransformer)


# ── Transformer tests ──


class TestIdentityTransformer:
    @pytest.mark.asyncio
    async def test_returns_original_question(self) -> None:
        t = IdentityTransformer()
        result = await t.transform("what is RAG?")
        assert result == ["what is RAG?"]


class TestHyDETransformer:
    @pytest.mark.asyncio
    async def test_returns_question_and_answer(self) -> None:
        provider = AsyncMock()
        provider.generate.return_value = MagicMock(content="RAG is retrieval-augmented generation.")

        t = HyDETransformer(provider=provider)
        result = await t.transform("what is RAG?")
        assert len(result) == 2
        assert result[0] == "what is RAG?"
        assert "retrieval-augmented" in result[1]

    @pytest.mark.asyncio
    async def test_falls_back_on_failure(self) -> None:
        provider = AsyncMock()
        provider.generate.side_effect = RuntimeError("LLM unavailable")

        t = HyDETransformer(provider=provider)
        result = await t.transform("what is RAG?")
        assert result == ["what is RAG?"]


class TestMultiQueryTransformer:
    @pytest.mark.asyncio
    async def test_returns_original_plus_variants(self) -> None:
        provider = AsyncMock()
        provider.generate.return_value = MagicMock(
            content="What is RAG in AI?\nHow does retrieval augmentation work?",
        )

        t = MultiQueryTransformer(provider=provider, num_variants=3)
        result = await t.transform("what is RAG?")
        assert result[0] == "what is RAG?"
        assert len(result) >= 2

    @pytest.mark.asyncio
    async def test_falls_back_on_failure(self) -> None:
        provider = AsyncMock()
        provider.generate.side_effect = RuntimeError("LLM unavailable")

        t = MultiQueryTransformer(provider=provider)
        result = await t.transform("what is RAG?")
        assert result == ["what is RAG?"]


# ── RAGConfig field tests ──


class TestRAGConfigQueryTransform:
    def test_defaults(self) -> None:
        config = RAGConfig()
        assert config.query_transform == "none"
        assert config.hyde_max_tokens == 256
        assert config.multi_query_variants == 3

    def test_custom_values(self) -> None:
        config = RAGConfig(query_transform="hyde", hyde_max_tokens=512, multi_query_variants=5)
        assert config.query_transform == "hyde"
        assert config.hyde_max_tokens == 512


# ── Pipeline integration tests ──


class TestPipelineQueryTransform:
    def test_lazy_init_creates_identity_by_default(self) -> None:
        config = RAGConfig()
        pipeline = RAGPipeline(
            llm_provider=MagicMock(),
            config=config,
            retriever=MagicMock(),
        )
        transformer = pipeline._get_query_transformer()
        assert isinstance(transformer, IdentityTransformer)

    def test_lazy_init_creates_configured_transformer(self) -> None:
        provider = AsyncMock()
        config = RAGConfig(query_transform="hyde")
        pipeline = RAGPipeline(
            llm_provider=provider,
            config=config,
            retriever=MagicMock(),
        )
        transformer = pipeline._get_query_transformer()
        assert isinstance(transformer, HyDETransformer)

    def test_deduplicate_chunks(self) -> None:
        chunk_a = MagicMock(dataset="ds1", row_id="r1", score=0.9)
        chunk_b = MagicMock(dataset="ds1", row_id="r1", score=0.7)
        chunk_c = MagicMock(dataset="ds1", row_id="r2", score=0.8)

        result = RAGPipeline._deduplicate_chunks([chunk_a, chunk_b, chunk_c])
        assert len(result) == 2
        # Keeps the higher-scored version of r1
        ids = {(c.dataset, c.row_id) for c in result}
        assert ("ds1", "r1") in ids
        assert ("ds1", "r2") in ids

    def test_merge_tables(self) -> None:
        import pyarrow as pa

        t1 = pa.table({"id": ["a"], "score": [0.9]})
        t2 = pa.table({"id": ["b"], "score": [0.8]})

        result = RAGPipeline._merge_tables((t1, t2))
        assert result.num_rows == 2

    def test_merge_tables_empty(self) -> None:
        import pyarrow as pa

        t1 = pa.table({"id": []})
        t2 = pa.table({"id": []})

        result = RAGPipeline._merge_tables((t1, t2))
        assert result.num_rows == 0
