"""E2E tests for RAG API endpoints — M2 Day 15.

Uses MockLLMProvider via Lake override to test full API stack
without requiring a real LLM backend.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
from arrow_lake.config import ArrowLakeConfig, RAGConfig
from arrow_lake.rag.provider import LLMMessage, LLMResponse
from fastapi.testclient import TestClient

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _mock_llm_response(text: str) -> LLMResponse:
    return LLMResponse(
        content=text,
        model="mock-model",
        usage={"prompt_tokens": 20, "completion_tokens": 10, "total_tokens": 30},
        finish_reason="stop",
        provider="mock",
    )


class MockLLMProvider:
    """A mock LLM provider for E2E tests."""

    def __init__(self) -> None:
        self._call_count = 0

    async def generate(self, messages: list[LLMMessage]) -> LLMResponse:
        self._call_count += 1
        last_msg = messages[-1].content if messages else ""
        if "entity" in last_msg.lower() or "extract" in last_msg.lower():
            return _mock_llm_response('{"entities": ["entity1", "entity2"]}')
        return _mock_llm_response("This is a mock RAG response.")

    async def generate_stream(self, messages: list[LLMMessage]):
        response = await self.generate(messages)
        for word in response.content.split():
            yield word + " "

    async def close(self) -> None:
        pass


def _mock_retriever(question: str, dataset_name: str, top_k: int):
    """Return a mock PyArrow table."""
    import pyarrow as pa

    return pa.table({
        "text": ["Document about Python programming.", "Python data science guide."],
        "row_id": ["doc-1", "doc-2"],
        "_score": [0.95, 0.85],
    })


def _create_app_with_rag() -> TestClient:
    """Create a test client with RAG properly wired up."""
    from arrow_lake.api.app import create_app
    from arrow_lake.rag.pipeline import RAGPipeline
    from arrow_lake.rag.session import SessionStore

    config = ArrowLakeConfig()
    config.rag = RAGConfig(enabled=True, default_top_k=3, enable_citations=True)
    app = create_app(config=config)

    # Replace Lake's RAG pipeline with mock
    mock_lake = MagicMock()

    provider = MockLLMProvider()
    session_store = SessionStore()
    pipeline = RAGPipeline(
        llm_provider=provider,
        config=config.rag,
        retriever=_mock_retriever,
        session_store=session_store,
    )

    mock_lake.rag_query = AsyncMock(wraps=pipeline.query)
    mock_lake.rag_query_stream = pipeline.query_stream
    mock_lake.rag_extract = AsyncMock(wraps=pipeline.extract_entities)
    mock_lake.rag_get_history = lambda sid: session_store.get_history(sid)
    app.state.lake = mock_lake

    return TestClient(app)


# ---------------------------------------------------------------------------
# E2E Tests
# ---------------------------------------------------------------------------


class TestRAGAPIE2E:
    @pytest.fixture()
    def client(self) -> TestClient:
        return _create_app_with_rag()

    def test_full_query_flow(self, client: TestClient) -> None:
        """E2E: POST /api/v2/rag/query returns valid response."""
        resp = client.post(
            "/api/v2/rag/query",
            json={
                "question": "Tell me about Python",
                "dataset_name": "documents",
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        assert "answer" in data
        assert isinstance(data["answer"], str)
        assert len(data["answer"]) > 0
        assert data["retrieval_count"] > 0
        assert isinstance(data["citations"], list)
        assert data["latency_ms"] is not None

    def test_query_with_session(self, client: TestClient) -> None:
        """E2E: Query with session_id persists history."""
        # Turn 1
        resp1 = client.post(
            "/api/v2/rag/query",
            json={
                "question": "What is Python?",
                "dataset_name": "documents",
                "session_id": "test-session",
            },
        )
        assert resp1.status_code == 200

        # Verify history was saved
        resp2 = client.get("/api/v2/rag/history/test-session")
        assert resp2.status_code == 200
        history = resp2.json()
        assert history["session_id"] == "test-session"
        assert len(history["turns"]) == 1
        assert history["turns"][0]["question"] == "What is Python?"

    def test_extract_flow(self, client: TestClient) -> None:
        """E2E: POST /api/v2/rag/extract returns entity response."""
        resp = client.post(
            "/api/v2/rag/extract",
            json={
                "dataset_name": "documents",
                "text_column": "text",
                "top_k": 3,
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        assert "answer" in data
        assert "entities" in data["answer"]
        assert data["retrieval_count"] > 0

    def test_templates_endpoint(self, client: TestClient) -> None:
        """E2E: GET /api/v2/rag/templates returns template list."""
        resp = client.get("/api/v2/rag/templates")
        assert resp.status_code == 200
        data = resp.json()
        assert "templates" in data
        assert len(data["templates"]) >= 3

        names = {t["name"] for t in data["templates"]}
        assert "default_qa" in names
        assert "entity_extract" in names
        assert "summarize" in names

    def test_history_nonexistent_session(self, client: TestClient) -> None:
        """E2E: GET /api/v2/rag/history for nonexistent session returns empty."""
        resp = client.get("/api/v2/rag/history/nonexistent")
        assert resp.status_code == 200
        data = resp.json()
        assert data["session_id"] == "nonexistent"
        assert data["turns"] == []

    def test_validation_errors(self, client: TestClient) -> None:
        """E2E: Invalid requests return 422."""
        # Missing question
        resp = client.post("/api/v2/rag/query", json={"dataset_name": "docs"})
        assert resp.status_code == 422

        # Missing dataset_name
        resp = client.post("/api/v2/rag/query", json={"question": "Q"})
        assert resp.status_code == 422

        # Empty question
        resp = client.post(
            "/api/v2/rag/query",
            json={"question": "", "dataset_name": "docs"},
        )
        assert resp.status_code == 422

    def test_stream_flow(self, client: TestClient) -> None:
        """E2E: POST /api/v2/rag/query/stream returns SSE events."""
        resp = client.post(
            "/api/v2/rag/query/stream",
            json={
                "question": "Tell me about Python",
                "dataset_name": "documents",
            },
        )
        assert resp.status_code == 200
        assert "text/event-stream" in resp.headers["content-type"]

        content = resp.text
        # Check SSE event types
        assert "event: metadata" in content
        assert "event: content" in content
        assert "event: done" in content
        # Check actual content was streamed
        assert "mock" in content.lower()

    def test_multi_turn_conversation(self, client: TestClient) -> None:
        """E2E: Multiple turns in a session accumulate correctly."""
        session_id = "multi-turn-test"

        for i in range(3):
            resp = client.post(
                "/api/v2/rag/query",
                json={
                    "question": f"Question {i}",
                    "dataset_name": "documents",
                    "session_id": session_id,
                },
            )
            assert resp.status_code == 200

        # Verify 3 turns
        resp = client.get(f"/api/v2/rag/history/{session_id}")
        assert resp.status_code == 200
        history = resp.json()
        assert len(history["turns"]) == 3
        assert history["turns"][0]["question"] == "Question 0"
        assert history["turns"][2]["question"] == "Question 2"
