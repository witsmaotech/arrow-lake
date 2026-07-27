"""Tests for RAG batch query pipeline."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest
from arrow_lake.config import RAGConfig
from arrow_lake.rag.pipeline import RAGPipeline, RAGResponse


@pytest.fixture()
def pipeline() -> RAGPipeline:
    import pyarrow as pa

    llm = AsyncMock()
    llm.generate = AsyncMock(return_value=type("R", (), {
        "content": "answer", "usage": {},
    })())
    config = RAGConfig()
    def retriever(q, d, k, s):
        return pa.table({"text_content": ["doc"], "_score": [0.9]})
    return RAGPipeline(llm, config, retriever)


@pytest.mark.asyncio()
async def test_batch_query_returns_all(pipeline: RAGPipeline) -> None:
    results = await pipeline.batch_query(["q1", "q2", "q3"], "ds1", concurrency=2)
    assert len(results) == 3
    assert all(isinstance(r, RAGResponse) for r in results)


@pytest.mark.asyncio()
async def test_batch_query_empty(pipeline: RAGPipeline) -> None:
    results = await pipeline.batch_query([], "ds1")
    assert results == []


@pytest.mark.asyncio()
async def test_batch_query_single(pipeline: RAGPipeline) -> None:
    results = await pipeline.batch_query(["q1"], "ds1", concurrency=1)
    assert len(results) == 1
    assert results[0].answer == "answer"
