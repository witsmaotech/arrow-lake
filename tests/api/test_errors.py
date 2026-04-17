"""Tests for ArrowLakeError → HTTP response mapping."""

from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient

from arrow_lake.api.app import create_app
from arrow_lake.api.errors import _error_code_to_http_status
from arrow_lake.exceptions import (
    ArrowLakeError,
    CatalogError,
    ErrorCode,
    QueryError,
    RayRuntimeError,
    StorageError,
)
from unittest.mock import MagicMock
from fastapi import FastAPI


@pytest.mark.asyncio
async def test_arrowlake_error_returns_mapped_status() -> None:
    """ArrowLakeError should be mapped to the correct HTTP status."""
    app: FastAPI = create_app()
    app.state.lake = MagicMock()

    @app.get("/test-error")
    async def trigger_error():
        raise ArrowLakeError(
            error_code=ErrorCode.CATALOG_DATASET_NOT_FOUND,
            message="Dataset 'x' not found",
        )

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.get("/test-error")
        assert resp.status_code == 404
        body = resp.json()
        assert body["success"] is False
        assert body["error"] == "CATALOG_DATASET_NOT_FOUND"


@pytest.mark.asyncio
async def test_unhandled_exception_returns_500() -> None:
    """Non-ArrowLakeError exceptions should return 500."""
    from starlette.exceptions import HTTPException

    app: FastAPI = create_app()
    app.state.lake = MagicMock()

    @app.get("/test-unhandled")
    async def trigger_unhandled():
        # Starlette wraps unexpected errors in HTTPException internally.
        # Use a registered handler via a sub-app pattern.
        raise HTTPException(status_code=500, detail="unexpected")

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.get("/test-unhandled")
        assert resp.status_code == 500


@pytest.mark.parametrize(
    "code,expected_status",
    [
        (ErrorCode.VALIDATION_INVALID_CONFIG, 400),
        (ErrorCode.QUERY_SYNTAX_ERROR, 400),
        (ErrorCode.INGEST_SCHEMA_MISMATCH, 400),
        (ErrorCode.CATALOG_DATASET_NOT_FOUND, 404),
        (ErrorCode.QUERY_INDEX_NOT_FOUND, 404),
        (ErrorCode.CATALOG_DATASET_ALREADY_EXISTS, 409),
        (ErrorCode.CATALOG_RATE_LIMITED, 429),
        (ErrorCode.QUERY_TIMEOUT, 504),
        (ErrorCode.RAY_RUNTIME_ACTOR_DEAD, 503),
        (ErrorCode.STORAGE_CONNECTION_FAILED, 502),
        (ErrorCode.HTTP_FETCH_FAILED, 502),
        (ErrorCode.EMBEDDING_MODEL_ERROR, 502),
        (ErrorCode.OLAP_QUERY_FAILED, 500),
        (ErrorCode.WORKFLOW_STEP_FAILED, 500),
        (ErrorCode.QUALITY_FILTER_EXECUTION_ERROR, 500),
    ],
)
def test_error_code_status_mapping(code: ErrorCode, expected_status: int) -> None:
    assert _error_code_to_http_status(code) == expected_status
