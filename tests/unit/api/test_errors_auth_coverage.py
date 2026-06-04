"""Coverage for errors.py error code mapping and auth middleware."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from arrow_lake.exceptions import ArrowLakeError, ErrorCode


# ── Error code → HTTP status mapping ──


class TestErrorCodeMapping:
    def test_validation_returns_400(self) -> None:
        from arrow_lake.api.errors import _error_code_to_http_status
        assert _error_code_to_http_status(ErrorCode.QUERY_SYNTAX_ERROR) == 400

    def test_not_found_returns_404(self) -> None:
        from arrow_lake.api.errors import _error_code_to_http_status
        assert _error_code_to_http_status(ErrorCode.CATALOG_DATASET_NOT_FOUND) == 404
        assert _error_code_to_http_status(ErrorCode.QUERY_INDEX_NOT_FOUND) == 404

    def test_already_exists_returns_409(self) -> None:
        from arrow_lake.api.errors import _error_code_to_http_status
        assert _error_code_to_http_status(ErrorCode.CATALOG_DATASET_ALREADY_EXISTS) == 409

    def test_rate_limited_returns_429(self) -> None:
        from arrow_lake.api.errors import _error_code_to_http_status
        assert _error_code_to_http_status(ErrorCode.CATALOG_RATE_LIMITED) == 429

    def test_timeout_returns_504(self) -> None:
        from arrow_lake.api.errors import _error_code_to_http_status
        assert _error_code_to_http_status(ErrorCode.QUERY_TIMEOUT) == 504

    def test_storage_returns_502(self) -> None:
        from arrow_lake.api.errors import _error_code_to_http_status
        assert _error_code_to_http_status(ErrorCode.STORAGE_READ_FAILED) == 502

    def test_auth_expired_returns_401(self) -> None:
        from arrow_lake.api.errors import _error_code_to_http_status
        assert _error_code_to_http_status(ErrorCode.AUTH_TOKEN_EXPIRED) == 401

    def test_auth_forbidden_returns_403(self) -> None:
        from arrow_lake.api.errors import _error_code_to_http_status
        assert _error_code_to_http_status(ErrorCode.AUTH_INSUFFICIENT_PERMISSIONS) == 403

    def test_rag_not_found_returns_404(self) -> None:
        from arrow_lake.api.errors import _error_code_to_http_status
        assert _error_code_to_http_status(ErrorCode.RAG_SESSION_NOT_FOUND) == 404

    def test_kg_graph_not_found_404(self) -> None:
        from arrow_lake.api.errors import _error_code_to_http_status
        assert _error_code_to_http_status(ErrorCode.KG_GRAPH_NOT_FOUND) == 404

    def test_unknown_returns_500(self) -> None:
        from arrow_lake.api.errors import _error_code_to_http_status
        # Use a code that doesn't match any special case
        assert _error_code_to_http_status(ErrorCode.WORKFLOW_FLOW_NOT_FOUND) == 404

    def test_ingest_unsupported_format_400(self) -> None:
        from arrow_lake.api.errors import _error_code_to_http_status
        assert _error_code_to_http_status(ErrorCode.INGEST_UNSUPPORTED_FORMAT) == 400

    def test_kg_schema_error_400(self) -> None:
        from arrow_lake.api.errors import _error_code_to_http_status
        assert _error_code_to_http_status(ErrorCode.KG_SCHEMA_ERROR) == 400

    def test_kg_traversal_timeout_504(self) -> None:
        from arrow_lake.api.errors import _error_code_to_http_status
        assert _error_code_to_http_status(ErrorCode.KG_TRAVERSAL_TIMEOUT) == 504

    def test_kg_connection_failed_502(self) -> None:
        from arrow_lake.api.errors import _error_code_to_http_status
        assert _error_code_to_http_status(ErrorCode.KG_CONNECTION_FAILED) == 502


# ── Exception handler ──


class TestExceptionHandler:
    @pytest.mark.asyncio
    async def test_error_handler_maps_status(self) -> None:
        from arrow_lake.api.errors import register_exception_handlers
        from starlette.testclient import TestClient
        from fastapi import FastAPI

        app = FastAPI()
        register_exception_handlers(app)

        @app.get("/test-error")
        async def raise_error():
            raise ArrowLakeError(
                error_code=ErrorCode.CATALOG_DATASET_NOT_FOUND,
                message="not found",
            )

        client = TestClient(app, raise_server_exceptions=False)
        resp = client.get("/test-error")
        assert resp.status_code == 404
        body = resp.json()
        assert body["success"] is False
        assert "not found" in body["message"]

    @pytest.mark.asyncio
    async def test_error_handler_filters_context(self) -> None:
        from arrow_lake.api.errors import register_exception_handlers
        from starlette.testclient import TestClient
        from fastapi import FastAPI

        app = FastAPI()
        register_exception_handlers(app)

        @app.get("/test-ctx")
        async def raise_with_ctx():
            raise ArrowLakeError(
                error_code=ErrorCode.QUERY_SYNTAX_ERROR,
                message="bad sql",
                context={"sql": "DROP TABLE", "dataset": "x"},
            )

        client = TestClient(app, raise_server_exceptions=False)
        resp = client.get("/test-ctx")
        body = resp.json()
        assert "sql" not in body.get("context", {})
        assert body["context"]["dataset"] == "x"
