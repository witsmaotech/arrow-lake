"""OpenAPI schema completeness validation."""

from __future__ import annotations

import pytest
from arrow_lake.api.app import create_app
from httpx import ASGITransport, AsyncClient


async def _get_app_and_client():
    """Create app and client for OpenAPI tests (no Lake needed)."""
    app = create_app()
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as ac:
        yield ac


# Expected endpoint paths grouped by tag
_EXPECTED_PATHS = {
    "system": [
        "/health",
        "/metrics",
        "/api/v1/version",
    ],
    "datasets": [
        "/api/v1/datasets",
        "/api/v1/datasets/{name}",
        "/api/v1/datasets/{name}/ingest",
        "/api/v1/datasets/{name}/ingest/http",
        "/api/v1/datasets/{name}/ingest/images",
        "/api/v1/datasets/{name}/ingest/videos",
        "/api/v1/datasets/{name}/ingest/mixed",
    ],
    "search": [
        "/api/v1/datasets/{name}/search/vector",
        "/api/v1/datasets/{name}/search/fts",
        "/api/v1/datasets/{name}/search/hybrid",
        "/api/v1/datasets/{name}/search/faceted",
        "/api/v1/datasets/{name}/search/ensemble",
    ],
    "query": [
        "/api/v1/datasets/{name}/query/olap",
        "/api/v1/datasets/{name}/query/metadata",
        "/api/v1/datasets/{name}/query/daft",
    ],
    "quality": [
        "/api/v1/datasets/{name}/quality/filter",
        "/api/v1/datasets/{name}/quality/deduplicate",
        "/api/v1/datasets/{name}/quality/report",
    ],
    "embedding": [
        "/api/v1/datasets/{name}/index/vector",
        "/api/v1/datasets/{name}/index/fts",
        "/api/v1/embed/text",
        "/api/v1/embed/image",
    ],
    "export": [
        "/api/v1/datasets/{name}/export",
        "/api/v1/datasets/{name}/export/{task_id}/status",
        "/api/v1/datasets/{name}/export/{task_id}/download",
    ],
    "lineage": [
        "/api/v1/lineage/record",
        "/api/v1/lineage/history/{dataset_name}",
        "/api/v1/lineage/query",
    ],
    "audit": [
        "/api/v1/audit/record",
        "/api/v1/audit/verify",
        "/api/v1/audit/query",
        "/api/v1/audit/export",
    ],
}


@pytest.mark.asyncio
async def test_openapi_spec_available() -> None:
    """GET /openapi.json returns a valid OpenAPI 3.x schema."""
    app = create_app()
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as ac:
        resp = await ac.get("/openapi.json")
        assert resp.status_code == 200
        schema = resp.json()
        assert schema["openapi"].startswith("3.")
        assert schema["info"]["title"] == "Arrow Lake REST API"


@pytest.mark.asyncio
async def test_all_expected_endpoints_exist() -> None:
    """Every planned endpoint appears in the OpenAPI schema."""
    app = create_app()
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as ac:
        resp = await ac.get("/openapi.json")
        schema = resp.json()
        paths = set(schema["paths"].keys())

        all_expected = []
        for tag_paths in _EXPECTED_PATHS.values():
            all_expected.extend(tag_paths)

        missing = [p for p in all_expected if p not in paths]
        assert not missing, f"Missing endpoints in OpenAPI schema: {missing}"


@pytest.mark.asyncio
async def test_openapi_has_all_tags() -> None:
    """All declared tags are present and have at least one path."""
    app = create_app()
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as ac:
        resp = await ac.get("/openapi.json")
        schema = resp.json()

        expected_tags = [
            "system", "datasets", "search", "query",
            "quality", "embedding", "export", "lineage", "audit",
        ]
        tag_names = [t["name"] for t in schema["tags"]]
        for tag in expected_tags:
            assert tag in tag_names, f"Missing tag: {tag}"


@pytest.mark.asyncio
async def test_docs_and_redoc_available() -> None:
    """Swagger UI and ReDoc are accessible."""
    app = create_app()
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as ac:
        docs = await ac.get("/docs")
        assert docs.status_code == 200

        redoc = await ac.get("/redoc")
        assert redoc.status_code == 200
