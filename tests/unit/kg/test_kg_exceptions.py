"""Unit tests for KG error codes and KGError exception."""

from __future__ import annotations

from arrow_lake.exceptions import ErrorCode


class TestKGErrorCode:
    """Tests for Knowledge Graph error codes."""

    def test_connection_failed(self) -> None:
        assert ErrorCode.KG_CONNECTION_FAILED == "KG_CONNECTION_FAILED"

    def test_schema_error(self) -> None:
        assert ErrorCode.KG_SCHEMA_ERROR == "KG_SCHEMA_ERROR"

    def test_query_failed(self) -> None:
        assert ErrorCode.KG_QUERY_FAILED == "KG_QUERY_FAILED"

    def test_traversal_timeout(self) -> None:
        assert ErrorCode.KG_TRAVERSAL_TIMEOUT == "KG_TRAVERSAL_TIMEOUT"

    def test_build_failed(self) -> None:
        assert ErrorCode.KG_BUILD_FAILED == "KG_BUILD_FAILED"

    def test_extract_failed(self) -> None:
        assert ErrorCode.KG_EXTRACT_FAILED == "KG_EXTRACT_FAILED"

    def test_graph_not_found(self) -> None:
        assert ErrorCode.KG_GRAPH_NOT_FOUND == "KG_GRAPH_NOT_FOUND"

    def test_all_have_kg_prefix(self) -> None:
        for name in dir(ErrorCode):
            if name.startswith("KG_"):
                assert name == name.upper(), f"{name} should be SCREAMING_SNAKE_CASE"
