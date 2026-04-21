"""Tests for RAG error codes and RAGError — M2 Day 1."""

from __future__ import annotations

from arrow_lake.exceptions import ArrowLakeError, ErrorCode, RAGError


class TestRAGErrorCodes:
    def test_retrieval_failed(self) -> None:
        assert ErrorCode.RAG_RETRIEVAL_FAILED == "RAG_RETRIEVAL_FAILED"

    def test_generation_failed(self) -> None:
        assert ErrorCode.RAG_GENERATION_FAILED == "RAG_GENERATION_FAILED"

    def test_context_too_long(self) -> None:
        assert ErrorCode.RAG_CONTEXT_TOO_LONG == "RAG_CONTEXT_TOO_LONG"

    def test_provider_error(self) -> None:
        assert ErrorCode.RAG_PROVIDER_ERROR == "RAG_PROVIDER_ERROR"

    def test_stream_error(self) -> None:
        assert ErrorCode.RAG_STREAM_ERROR == "RAG_STREAM_ERROR"

    def test_template_not_found(self) -> None:
        assert ErrorCode.RAG_TEMPLATE_NOT_FOUND == "RAG_TEMPLATE_NOT_FOUND"

    def test_session_not_found(self) -> None:
        assert ErrorCode.RAG_SESSION_NOT_FOUND == "RAG_SESSION_NOT_FOUND"

    def test_all_have_rag_prefix(self) -> None:
        for code in ErrorCode:
            if code.startswith("RAG_"):
                assert code.isupper(), f"{code} should be UPPER_CASE"


class TestRAGError:
    def test_inherits_arrow_lake_error(self) -> None:
        assert issubclass(RAGError, ArrowLakeError)

    def test_construction(self) -> None:
        exc = RAGError(
            error_code=ErrorCode.RAG_GENERATION_FAILED,
            message="LLM call failed",
        )
        assert exc.error_code == ErrorCode.RAG_GENERATION_FAILED
        assert exc.message == "LLM call failed"
        assert "[RAG_GENERATION_FAILED]" in str(exc)

    def test_with_context(self) -> None:
        exc = RAGError(
            error_code=ErrorCode.RAG_PROVIDER_ERROR,
            message="provider unavailable",
            context={"provider": "openai", "attempt": 3},
        )
        assert exc.context["provider"] == "openai"
