"""Tests for RAG API router — M2 Day 12."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
from arrow_lake.config import ArrowLakeConfig
from arrow_lake.rag.pipeline import RAGCitation, RAGResponse
from fastapi.testclient import TestClient

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _mock_rag_response(
    answer: str = "Test answer",
    citations: list[RAGCitation] | None = None,
    retrieval_count: int = 2,
) -> RAGResponse:
    return RAGResponse(
        answer=answer,
        citations=tuple(citations or [RAGCitation(0, "docs", "r1", 0.9, "excerpt")]),
        retrieval_count=retrieval_count,
        context_tokens=50,
        llm_usage={"total_tokens": 15},
        latency_ms=100.0,
    )


def _create_client() -> TestClient:
    """Create a test client with mocked Lake and auth disabled."""
    from arrow_lake.api.app import create_app

    config = ArrowLakeConfig()
    config.api.enabled = False
    config.api.api_key = ""
    app = create_app(config=config)

    # Mock the lake
    mock_lake = MagicMock()
    mock_lake.rag_query = AsyncMock(return_value=_mock_rag_response())
    mock_lake.rag_extract = AsyncMock(return_value=_mock_rag_response("Entities found"))
    app.state.lake = mock_lake

    return TestClient(app)


# ---------------------------------------------------------------------------
# POST /api/v1/rag/query
# ---------------------------------------------------------------------------


class TestRAGQueryEndpoint:
    @pytest.fixture()
    def client(self) -> TestClient:
        return _create_client()

    def test_query_success(self, client: TestClient) -> None:
        resp = client.post(
            "/api/v1/rag/query",
            json={"question": "What is AI?", "dataset_name": "docs"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["answer"] == "Test answer"
        assert data["retrieval_count"] == 2
        assert len(data["citations"]) == 1

    def test_query_with_options(self, client: TestClient) -> None:
        resp = client.post(
            "/api/v1/rag/query",
            json={
                "question": "Q",
                "dataset_name": "docs",
                "top_k": 5,
                "retrieval_strategy": "hybrid",
                "session_id": "sess-1",
            },
        )
        assert resp.status_code == 200

    def test_query_missing_question(self, client: TestClient) -> None:
        resp = client.post(
            "/api/v1/rag/query",
            json={"dataset_name": "docs"},
        )
        assert resp.status_code == 422

    def test_query_missing_dataset(self, client: TestClient) -> None:
        resp = client.post(
            "/api/v1/rag/query",
            json={"question": "Q"},
        )
        assert resp.status_code == 422


# ---------------------------------------------------------------------------
# POST /api/v1/rag/query/stream (SSE)
# ---------------------------------------------------------------------------


class TestRAGStreamEndpoint:
    @pytest.fixture()
    def client(self) -> TestClient:
        from arrow_lake.api.app import create_app

        config = ArrowLakeConfig()
        config.api.enabled = False
        config.api.api_key = ""
        app = create_app(config=config)

        mock_lake = MagicMock()

        async def mock_stream(*args, **kwargs):
            yield "Hello"
            yield " world"

        mock_lake.rag_query_stream = mock_stream
        app.state.lake = mock_lake

        return TestClient(app)

    def test_stream_returns_sse(self, client: TestClient) -> None:
        resp = client.post(
            "/api/v1/rag/query/stream",
            json={"question": "What is AI?", "dataset_name": "docs"},
        )
        assert resp.status_code == 200
        assert resp.headers["content-type"] == "text/event-stream; charset=utf-8"

        content = resp.text
        assert "event: content" in content
        assert "event: done" in content
        assert '"data":"Hello"' in content or '"data": "Hello"' in content

    def test_stream_includes_metadata(self, client: TestClient) -> None:
        resp = client.post(
            "/api/v1/rag/query/stream",
            json={"question": "Q", "dataset_name": "docs", "top_k": 5},
        )
        assert resp.status_code == 200
        content = resp.text
        assert "event: metadata" in content
        assert '"dataset_name":"docs"' in content or '"dataset_name": "docs"' in content

    def test_stream_error_yields_error_event(self) -> None:
        from arrow_lake.api.app import create_app

        config = ArrowLakeConfig()
        config.api.enabled = False
        config.api.api_key = ""
        app = create_app(config=config)

        mock_lake = MagicMock()

        async def mock_stream_fail(*args, **kwargs):
            yield "OK"
            raise RuntimeError("LLM connection failed")

        mock_lake.rag_query_stream = mock_stream_fail
        app.state.lake = mock_lake

        client = TestClient(app)
        resp = client.post(
            "/api/v1/rag/query/stream",
            json={"question": "Q", "dataset_name": "docs"},
        )
        assert resp.status_code == 200
        content = resp.text
        assert "event: content" in content
        assert "event: error" in content
        assert "internal error" in content

    def test_stream_empty_response(self) -> None:
        from arrow_lake.api.app import create_app

        config = ArrowLakeConfig()
        config.api.enabled = False
        config.api.api_key = ""
        app = create_app(config=config)

        mock_lake = MagicMock()

        async def mock_stream_empty(*args, **kwargs):
            return
            yield  # type: ignore[unreachable]

        mock_lake.rag_query_stream = mock_stream_empty
        app.state.lake = mock_lake

        client = TestClient(app)
        resp = client.post(
            "/api/v1/rag/query/stream",
            json={"question": "Q", "dataset_name": "docs"},
        )
        assert resp.status_code == 200
        content = resp.text
        assert "event: metadata" in content
        assert "event: done" in content


# ---------------------------------------------------------------------------
# POST /api/v1/rag/extract
# ---------------------------------------------------------------------------


class TestRAGExtractEndpoint:
    @pytest.fixture()
    def client(self) -> TestClient:
        return _create_client()

    def test_extract_success(self, client: TestClient) -> None:
        resp = client.post(
            "/api/v1/rag/extract",
            json={"dataset_name": "docs"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["answer"] == "Entities found"

    def test_extract_with_options(self, client: TestClient) -> None:
        resp = client.post(
            "/api/v1/rag/extract",
            json={
                "dataset_name": "docs",
                "text_column": "body",
                "top_k": 10,
            },
        )
        assert resp.status_code == 200


# ---------------------------------------------------------------------------
# GET /api/v1/rag/templates
# ---------------------------------------------------------------------------


class TestRAGTemplatesEndpoint:
    @pytest.fixture()
    def client(self) -> TestClient:
        return _create_client()

    def test_templates_returns_list(self, client: TestClient) -> None:
        resp = client.get("/api/v1/rag/templates")
        assert resp.status_code == 200
        data = resp.json()
        assert "templates" in data
        assert len(data["templates"]) >= 3  # default_qa, entity_extract, summarize

    def test_templates_have_required_fields(self, client: TestClient) -> None:
        resp = client.get("/api/v1/rag/templates")
        data = resp.json()
        for tmpl in data["templates"]:
            assert "name" in tmpl
            assert "type" in tmpl
            assert "description" in tmpl


# ---------------------------------------------------------------------------
# GET /api/v1/rag/history/{session_id}
# ---------------------------------------------------------------------------


class TestRAGHistoryEndpoint:
    @pytest.fixture()
    def client(self) -> TestClient:
        from arrow_lake.api.app import create_app

        config = ArrowLakeConfig()
        config.api.enabled = False
        config.api.api_key = ""
        config.auth.allow_unauthenticated_access = True
        app = create_app(config=config)

        mock_lake = MagicMock()
        mock_lake.rag_get_history.return_value = [
            {"turn_id": 1, "question": "Q1", "answer": "A1"},
        ]
        app.state.lake = mock_lake

        return TestClient(app)

    def test_history_returns_turns(self, client: TestClient) -> None:
        resp = client.get("/api/v1/rag/history/sess-1")
        assert resp.status_code == 200
        data = resp.json()
        assert data["session_id"] == "sess-1"
        assert len(data["turns"]) == 1
        assert data["turns"][0]["question"] == "Q1"
