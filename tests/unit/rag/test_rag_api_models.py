"""Tests for RAG API Pydantic models — M2 Day 11."""

from __future__ import annotations

import pytest
from arrow_lake.api.models.rag import (
    RAGCitationResponse,
    RAGExtractRequest,
    RAGExtractResponse,
    RAGHistoryResponse,
    RAGQueryRequest,
    RAGQueryResponse,
    RAGSessionSummary,
    RAGTemplateInfo,
    RAGTemplatesResponse,
)
from pydantic import ValidationError

# ---------------------------------------------------------------------------
# RAGQueryRequest
# ---------------------------------------------------------------------------


class TestRAGQueryRequest:
    def test_minimal(self) -> None:
        req = RAGQueryRequest(question="What is AI?", dataset_name="docs")
        assert req.question == "What is AI?"
        assert req.dataset_name == "docs"
        assert req.top_k is None
        assert req.retrieval_strategy is None
        assert req.template_name is None
        assert req.session_id is None

    def test_full(self) -> None:
        req = RAGQueryRequest(
            question="What is AI?",
            dataset_name="docs",
            top_k=5,
            retrieval_strategy="hybrid",
            template_name="custom",
            session_id="sess-1",
        )
        assert req.top_k == 5
        assert req.retrieval_strategy == "hybrid"
        assert req.session_id == "sess-1"

    def test_question_required(self) -> None:
        with pytest.raises(ValidationError):
            RAGQueryRequest(dataset_name="docs")

    def test_dataset_name_required(self) -> None:
        with pytest.raises(ValidationError):
            RAGQueryRequest(question="Q?")

    def test_empty_question_rejected(self) -> None:
        with pytest.raises(ValidationError):
            RAGQueryRequest(question="", dataset_name="docs")

    def test_top_k_minimum(self) -> None:
        with pytest.raises(ValidationError):
            RAGQueryRequest(question="Q", dataset_name="d", top_k=0)


# ---------------------------------------------------------------------------
# RAGCitationResponse
# ---------------------------------------------------------------------------


class TestRAGCitationResponse:
    def test_construction(self) -> None:
        cite = RAGCitationResponse(
            chunk_index=0,
            dataset="docs",
            row_id="r1",
            score=0.95,
            text_excerpt="Some text",
        )
        assert cite.chunk_index == 0
        assert cite.dataset == "docs"
        assert cite.row_id == "r1"
        assert cite.score == 0.95


# ---------------------------------------------------------------------------
# RAGQueryResponse
# ---------------------------------------------------------------------------


class TestRAGQueryResponse:
    def test_minimal(self) -> None:
        resp = RAGQueryResponse(
            answer="Hello!",
            citations=[],
            retrieval_count=2,
        )
        assert resp.answer == "Hello!"
        assert resp.retrieval_count == 2
        assert resp.latency_ms is None
        assert resp.session_id is None

    def test_full(self) -> None:
        resp = RAGQueryResponse(
            answer="42",
            citations=[
                RAGCitationResponse(
                    chunk_index=0,
                    dataset="d",
                    row_id="r1",
                    score=0.9,
                    text_excerpt="text",
                )
            ],
            retrieval_count=5,
            context_tokens=100,
            latency_ms=50.5,
            session_id="sess-1",
        )
        assert len(resp.citations) == 1
        assert resp.latency_ms == 50.5


# ---------------------------------------------------------------------------
# RAGExtractRequest
# ---------------------------------------------------------------------------


class TestRAGExtractRequest:
    def test_minimal(self) -> None:
        req = RAGExtractRequest(dataset_name="docs")
        assert req.dataset_name == "docs"
        assert req.text_column == "text_content"
        assert req.top_k is None
        assert req.template_name is None

    def test_full(self) -> None:
        req = RAGExtractRequest(
            dataset_name="docs",
            text_column="body",
            top_k=10,
            template_name="custom_extract",
        )
        assert req.text_column == "body"
        assert req.top_k == 10


# ---------------------------------------------------------------------------
# RAGExtractResponse
# ---------------------------------------------------------------------------


class TestRAGExtractResponse:
    def test_construction(self) -> None:
        resp = RAGExtractResponse(
            answer='{"entities": ["Acme Corp"]}',
            retrieval_count=3,
            latency_ms=80.0,
        )
        assert "Acme Corp" in resp.answer
        assert resp.latency_ms == 80.0


# ---------------------------------------------------------------------------
# RAGTemplateInfo
# ---------------------------------------------------------------------------


class TestRAGTemplateInfo:
    def test_construction(self) -> None:
        tmpl = RAGTemplateInfo(
            name="default_qa",
            type="qa",
            description="Default Q&A template",
        )
        assert tmpl.name == "default_qa"
        assert tmpl.type == "qa"


# ---------------------------------------------------------------------------
# RAGTemplatesResponse
# ---------------------------------------------------------------------------


class TestRAGTemplatesResponse:
    def test_construction(self) -> None:
        resp = RAGTemplatesResponse(
            templates=[
                RAGTemplateInfo(name="default_qa", type="qa", description="Default"),
                RAGTemplateInfo(name="entity_extract", type="extract", description="Entities"),
            ]
        )
        assert len(resp.templates) == 2


# ---------------------------------------------------------------------------
# RAGSessionSummary
# ---------------------------------------------------------------------------


class TestRAGSessionSummary:
    def test_construction(self) -> None:
        s = RAGSessionSummary(
            session_id="sess-1",
            turn_count=3,
            last_question="What is AI?",
            last_timestamp=1745000000.0,
        )
        assert s.session_id == "sess-1"
        assert s.turn_count == 3


# ---------------------------------------------------------------------------
# RAGHistoryResponse
# ---------------------------------------------------------------------------


class TestRAGHistoryResponse:
    def test_construction(self) -> None:
        resp = RAGHistoryResponse(
            session_id="sess-1",
            turns=[
                {"turn_id": 1, "question": "Q1", "answer": "A1"},
                {"turn_id": 2, "question": "Q2", "answer": "A2"},
            ],
        )
        assert resp.session_id == "sess-1"
        assert len(resp.turns) == 2

    def test_empty_history(self) -> None:
        resp = RAGHistoryResponse(session_id="sess-x", turns=[])
        assert resp.turns == []
