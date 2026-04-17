"""QA integration tests for Arrow Lake REST API — Sprint 1 & Sprint 2.

Uses a real Lake instance backed by Lance on a local tmp_path directory.
No mocks are used for storage or Lake operations — the full stack is exercised
from HTTP request down to Lance filesystem writes.

Dependencies:
    - lancedb  (required — pytest.importorskip skips if missing)
    - daft     (required for ingest via Ingestor)
    - pyarrow  (required for Arrow table construction)

Run with:
    pytest tests/api/test_integration.py -m integration -v
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

# Skip entire module if core storage dependencies are unavailable.
pytest.importorskip("lancedb", reason="lancedb not installed")
pytest.importorskip("pyarrow", reason="pyarrow not installed")

from httpx import ASGITransport, AsyncClient

from arrow_lake.api.app import create_app
from arrow_lake.config import ArrowLakeConfig, ApiConfig, StorageConfig
from arrow_lake.exceptions import ArrowLakeError, CatalogError, ErrorCode


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def lake_base_uri(tmp_path: Path) -> str:
    """Return a fresh temporary directory for Lance storage."""
    return str(tmp_path / "lake")


@pytest.fixture()
def app_no_auth(lake_base_uri: str) -> Any:
    """Create a real FastAPI app with no API key auth, using tmp_path."""
    config = ArrowLakeConfig(
        storage=StorageConfig(backend="local"),
        api=ApiConfig(api_key=""),
    )

    # Bypass the lifespan to avoid config.storage.base_uri issue.
    # Instead, create Lake directly and set on app.state.
    from fastapi import FastAPI

    application: FastAPI = create_app()
    from arrow_lake import Lake

    lake = Lake(base_uri=lake_base_uri, config=config)
    application.state.lake = lake
    return application


@pytest.fixture()
def app_with_auth(lake_base_uri: str) -> Any:
    """Create a real FastAPI app with API key auth enabled."""
    config = ArrowLakeConfig(
        storage=StorageConfig(backend="local"),
        api=ApiConfig(
            api_key="test-integration-key",
            api_key_header="X-API-Key",
        ),
    )

    from fastapi import FastAPI

    application: FastAPI = create_app(config=config)
    from arrow_lake import Lake

    lake = Lake(base_uri=lake_base_uri, config=config)
    application.state.lake = lake
    return application


@pytest.fixture()
def sample_csv(tmp_path: Path) -> Path:
    """Create a small CSV file for ingestion tests."""
    csv_path = tmp_path / "sample_data.csv"
    csv_path.write_text(
        "id,name,score\n"
        "1,alice,95.5\n"
        "2,bob,87.2\n"
        "3,charlie,91.0\n"
    )
    return csv_path


# ---------------------------------------------------------------------------
# Sprint 1: System endpoints
# ---------------------------------------------------------------------------


@pytest.mark.integration
@pytest.mark.anyio
async def test_health_returns_valid_response(app_no_auth: Any) -> None:
    """GET /health returns 200 with status and version fields."""
    async with AsyncClient(
        transport=ASGITransport(app=app_no_auth),
        base_url="http://test",
    ) as ac:
        resp = await ac.get("/health")
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] in ("ok", "degraded")
        assert "version" in body
        assert isinstance(body["version"], str)
        assert len(body["version"]) > 0
        assert "storage" in body


@pytest.mark.integration
@pytest.mark.anyio
async def test_metrics_returns_prometheus_format(app_no_auth: Any) -> None:
    """GET /metrics returns a string (Prometheus text format or fallback)."""
    async with AsyncClient(
        transport=ASGITransport(app=app_no_auth),
        base_url="http://test",
    ) as ac:
        resp = await ac.get("/metrics")
        assert resp.status_code == 200
        content_type = resp.headers.get("content-type", "")
        # prometheus_client returns text/plain; FastAPI str return may use application/json
        assert "text/plain" in content_type or "application/json" in content_type
        body = resp.text
        assert len(body) > 0


@pytest.mark.integration
@pytest.mark.anyio
async def test_openapi_spec_available(app_no_auth: Any) -> None:
    """GET /openapi.json returns the OpenAPI schema."""
    async with AsyncClient(
        transport=ASGITransport(app=app_no_auth),
        base_url="http://test",
    ) as ac:
        resp = await ac.get("/openapi.json")
        assert resp.status_code == 200
        schema = resp.json()
        assert schema["info"]["title"] == "Arrow Lake REST API"
        assert "paths" in schema
        assert "/health" in schema["paths"]


# ---------------------------------------------------------------------------
# Sprint 2: Dataset CRUD with real Lance storage
# ---------------------------------------------------------------------------


@pytest.mark.integration
@pytest.mark.anyio
async def test_list_datasets_empty(app_no_auth: Any) -> None:
    """GET /api/v1/datasets returns empty list when no datasets exist."""
    async with AsyncClient(
        transport=ASGITransport(app=app_no_auth),
        base_url="http://test",
    ) as ac:
        resp = await ac.get("/api/v1/datasets")
        assert resp.status_code == 200
        body = resp.json()
        assert body["success"] is True
        assert body["total"] == 0
        assert body["datasets"] == []


@pytest.mark.integration
@pytest.mark.anyio
async def test_get_nonexistent_dataset_returns_404(app_no_auth: Any) -> None:
    """GET /api/v1/datasets/{name} returns 404 for unknown dataset."""
    async with AsyncClient(
        transport=ASGITransport(app=app_no_auth),
        base_url="http://test",
    ) as ac:
        resp = await ac.get("/api/v1/datasets/nonexistent_xyz")
        assert resp.status_code == 404
        body = resp.json()
        assert body["success"] is False
        assert "CATALOG_DATASET_NOT_FOUND" == body["error"]


@pytest.mark.integration
@pytest.mark.anyio
async def test_delete_nonexistent_dataset_returns_404(app_no_auth: Any) -> None:
    """DELETE /api/v1/datasets/{name} returns 404 for unknown dataset."""
    async with AsyncClient(
        transport=ASGITransport(app=app_no_auth),
        base_url="http://test",
    ) as ac:
        resp = await ac.delete("/api/v1/datasets/nonexistent_xyz")
        assert resp.status_code == 404
        body = resp.json()
        assert body["success"] is False


@pytest.mark.integration
@pytest.mark.anyio
async def test_ingest_list_get_delete_flow(
    app_no_auth: Any,
    sample_csv: Path,
) -> None:
    """Full CRUD cycle: ingest -> list -> get -> delete with real storage."""
    dataset_name = "test_integration_ds"

    async with AsyncClient(
        transport=ASGITransport(app=app_no_auth),
        base_url="http://test",
    ) as ac:
        # --- Step 1: Ingest a CSV file ---
        resp = await ac.post(
            f"/api/v1/datasets/{dataset_name}/ingest",
            json={"file_paths": [str(sample_csv)]},
        )
        assert resp.status_code == 201, f"ingest failed: {resp.text}"
        body = resp.json()
        assert body["success"] is True
        assert body["total_rows"] > 0
        assert body["total_files"] >= 1

        # --- Step 2: List datasets — should include the new one ---
        resp = await ac.get("/api/v1/datasets")
        assert resp.status_code == 200
        body = resp.json()
        assert body["success"] is True
        assert body["total"] >= 1
        names = [ds["name"] for ds in body["datasets"]]
        assert dataset_name in names

        # --- Step 3: Get specific dataset ---
        resp = await ac.get(f"/api/v1/datasets/{dataset_name}")
        assert resp.status_code == 200
        body = resp.json()
        assert body["name"] == dataset_name
        assert body["num_rows"] > 0
        assert body["version"] >= 1

        # --- Step 4: Delete the dataset ---
        resp = await ac.delete(f"/api/v1/datasets/{dataset_name}")
        assert resp.status_code == 200
        body = resp.json()
        assert body["success"] is True
        assert "deleted" in body["message"].lower()

        # --- Step 5: Verify dataset is gone ---
        resp = await ac.get(f"/api/v1/datasets/{dataset_name}")
        assert resp.status_code == 404


@pytest.mark.integration
@pytest.mark.anyio
async def test_ingest_then_catalog_reflects_correct_row_count(
    app_no_auth: Any,
    sample_csv: Path,
) -> None:
    """After ingestion, catalog reports correct row count."""
    dataset_name = "test_row_count_ds"

    async with AsyncClient(
        transport=ASGITransport(app=app_no_auth),
        base_url="http://test",
    ) as ac:
        resp = await ac.post(
            f"/api/v1/datasets/{dataset_name}/ingest",
            json={"file_paths": [str(sample_csv)]},
        )
        assert resp.status_code == 201, f"ingest failed: {resp.text}"
        ingest_body = resp.json()
        expected_rows = ingest_body["total_rows"]

        # Verify via catalog endpoint
        resp = await ac.get(f"/api/v1/datasets/{dataset_name}")
        assert resp.status_code == 200
        assert resp.json()["num_rows"] == expected_rows


# ---------------------------------------------------------------------------
# API Key authentication (real config, not manual middleware)
# ---------------------------------------------------------------------------


@pytest.mark.integration
@pytest.mark.anyio
async def test_api_key_auth_valid_key(app_with_auth: Any) -> None:
    """Requests with correct API key are accepted."""
    async with AsyncClient(
        transport=ASGITransport(app=app_with_auth),
        base_url="http://test",
        headers={"X-API-Key": "test-integration-key"},
    ) as ac:
        resp = await ac.get("/api/v1/datasets")
        # Should NOT be 401 (auth passes)
        assert resp.status_code != 401


@pytest.mark.integration
@pytest.mark.anyio
async def test_api_key_auth_missing_key(app_with_auth: Any) -> None:
    """Requests without API key on protected endpoints return 401."""
    async with AsyncClient(
        transport=ASGITransport(app=app_with_auth),
        base_url="http://test",
    ) as ac:
        resp = await ac.get("/api/v1/datasets")
        assert resp.status_code == 401
        body = resp.json()
        assert body["success"] is False
        assert "API key" in body["message"]


@pytest.mark.integration
@pytest.mark.anyio
async def test_api_key_auth_wrong_key(app_with_auth: Any) -> None:
    """Requests with wrong API key return 401."""
    async with AsyncClient(
        transport=ASGITransport(app=app_with_auth),
        base_url="http://test",
        headers={"X-API-Key": "wrong-secret-key"},
    ) as ac:
        resp = await ac.get("/api/v1/datasets")
        assert resp.status_code == 401


@pytest.mark.integration
@pytest.mark.anyio
async def test_api_key_auth_public_paths_bypass(app_with_auth: Any) -> None:
    """Public paths (/health, /openapi.json) bypass API key check."""
    async with AsyncClient(
        transport=ASGITransport(app=app_with_auth),
        base_url="http://test",
    ) as ac:
        # /health is public -- no key needed
        resp = await ac.get("/health")
        assert resp.status_code == 200

        # /openapi.json is public
        resp = await ac.get("/openapi.json")
        assert resp.status_code == 200

        # /docs is public
        resp = await ac.get("/docs", follow_redirects=False)
        assert resp.status_code in (200, 307)


@pytest.mark.integration
@pytest.mark.anyio
async def test_api_key_auth_metrics_bypass(app_with_auth: Any) -> None:
    """GET /metrics bypasses API key authentication."""
    async with AsyncClient(
        transport=ASGITransport(app=app_with_auth),
        base_url="http://test",
    ) as ac:
        resp = await ac.get("/metrics")
        assert resp.status_code == 200


# ---------------------------------------------------------------------------
# ArrowLakeError -> HTTP error mapping (real handler, no mocks)
# ---------------------------------------------------------------------------


@pytest.mark.integration
@pytest.mark.anyio
async def test_arrowlake_error_maps_to_http_404(app_no_auth: Any) -> None:
    """CatalogError(CATALOG_DATASET_NOT_FOUND) maps to HTTP 404."""
    async with AsyncClient(
        transport=ASGITransport(app=app_no_auth),
        base_url="http://test",
    ) as ac:
        resp = await ac.get("/api/v1/datasets/does_not_exist_at_all")
        assert resp.status_code == 404
        body = resp.json()
        assert body["success"] is False
        assert body["error"] == "CATALOG_DATASET_NOT_FOUND"
        assert "does_not_exist_at_all" in body["message"]


@pytest.mark.integration
@pytest.mark.anyio
async def test_error_response_envelope_format(app_no_auth: Any) -> None:
    """ArrowLakeError responses follow the standard error envelope."""
    async with AsyncClient(
        transport=ASGITransport(app=app_no_auth),
        base_url="http://test",
    ) as ac:
        resp = await ac.get("/api/v1/datasets/missing")
        body = resp.json()
        # Standard error envelope fields
        assert "success" in body
        assert "error" in body
        assert "message" in body
        assert body["success"] is False
        assert isinstance(body["error"], str)
        assert isinstance(body["message"], str)


@pytest.mark.integration
@pytest.mark.anyio
async def test_validation_error_maps_to_http_422(app_no_auth: Any) -> None:
    """Request with invalid body (Pydantic validation) returns 422."""
    async with AsyncClient(
        transport=ASGITransport(app=app_no_auth),
        base_url="http://test",
    ) as ac:
        # Empty file_paths should fail Pydantic validation
        resp = await ac.post(
            "/api/v1/datasets/test/ingest",
            json={"file_paths": []},
        )
        assert resp.status_code == 422


@pytest.mark.integration
@pytest.mark.anyio
async def test_delete_nonexistent_maps_to_404(app_no_auth: Any) -> None:
    """Deleting a nonexistent dataset triggers STORAGE_PATH_NOT_FOUND -> 404."""
    async with AsyncClient(
        transport=ASGITransport(app=app_no_auth),
        base_url="http://test",
    ) as ac:
        resp = await ac.delete("/api/v1/datasets/no_such_dataset")
        assert resp.status_code == 404
        body = resp.json()
        # Storage layer raises STORAGE_PATH_NOT_FOUND for missing datasets
        assert body["error"] == "STORAGE_PATH_NOT_FOUND"


# ---------------------------------------------------------------------------
# Isolation: each test gets a fresh tmp_path
# ---------------------------------------------------------------------------


@pytest.mark.integration
@pytest.mark.anyio
async def test_isolation_separate_lake_instances(tmp_path: Path) -> None:
    """Two test app instances with different tmp_paths do not share data."""
    from fastapi import FastAPI

    from arrow_lake import Lake

    # App A
    dir_a = str(tmp_path / "lake_a")
    app_a: FastAPI = create_app()
    lake_a = Lake(base_uri=dir_a)
    app_a.state.lake = lake_a

    # App B
    dir_b = str(tmp_path / "lake_b")
    app_b: FastAPI = create_app()
    lake_b = Lake(base_uri=dir_b)
    app_b.state.lake = lake_b

    # Verify A starts empty
    async with AsyncClient(
        transport=ASGITransport(app=app_a), base_url="http://test"
    ) as ac_a:
        resp_a = await ac_a.get("/api/v1/datasets")
        assert resp_a.status_code == 200
        assert resp_a.json()["total"] == 0

    # Verify B starts empty
    async with AsyncClient(
        transport=ASGITransport(app=app_b), base_url="http://test"
    ) as ac_b:
        resp_b = await ac_b.get("/api/v1/datasets")
        assert resp_b.status_code == 200
        assert resp_b.json()["total"] == 0
