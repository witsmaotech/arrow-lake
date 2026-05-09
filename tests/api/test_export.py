"""Tests for export endpoint."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from unittest.mock import MagicMock

import pytest
from arrow_lake.api.app import create_app
from arrow_lake.config import ArrowLakeConfig
from httpx import ASGITransport, AsyncClient


@dataclass(frozen=True)
class _FakeExportResult:
    dataset_name: str = "docs"
    output_path: str = "/tmp/docs.parquet"
    format: str = "parquet"
    row_count: int = 100
    column_count: int = 5
    file_size_bytes: int = 4096
    version: int = 2


@pytest.fixture
def mock_lake() -> MagicMock:
    lake = MagicMock()
    lake.export.return_value = _FakeExportResult()
    return lake


@pytest.fixture
async def client(mock_lake: MagicMock) -> AsyncClient:
    config = ArrowLakeConfig()
    config.api.api_key = "test-api-key"
    config.api.docs_enabled = False
    app = create_app(config=config)
    app.state.lake = mock_lake
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
        headers={"X-API-Key": "test-api-key"},
    ) as ac:
        yield ac


# ---------------------------------------------------------------------------
# Export (async)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_export_parquet(client: AsyncClient, mock_lake: MagicMock) -> None:
    resp = await client.post(
        "/api/v1/datasets/docs/export",
        json={"output_path": "output/docs.parquet"},
    )
    assert resp.status_code == 202
    body = resp.json()
    assert body["success"] is True
    assert body["task_id"] != ""
    assert body["status"] == "pending"

    # Wait for background task to complete
    await asyncio.sleep(0.1)

    mock_lake.export.assert_called_once()


@pytest.mark.asyncio
async def test_export_with_options(client: AsyncClient, mock_lake: MagicMock) -> None:
    resp = await client.post(
        "/api/v1/datasets/docs/export",
        json={
            "output_path": "output/docs.csv",
            "format": "csv",
            "columns": ["id", "text"],
            "compression": "gzip",
            "overwrite": True,
        },
    )
    assert resp.status_code == 202

    # Wait for background task to complete
    await asyncio.sleep(0.1)

    call_kwargs = mock_lake.export.call_args
    assert call_kwargs[1]["format"] == "csv"
    assert call_kwargs[1]["columns"] == ["id", "text"]
    assert call_kwargs[1]["compression"] == "gzip"
    assert call_kwargs[1]["overwrite"] is True


@pytest.mark.asyncio
async def test_export_path_traversal_rejected(client: AsyncClient) -> None:
    resp = await client.post(
        "/api/v1/datasets/docs/export",
        json={"output_path": "../../etc/passwd"},
    )
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_export_absolute_path_rejected(client: AsyncClient) -> None:
    resp = await client.post(
        "/api/v1/datasets/docs/export",
        json={"output_path": "/etc/passwd"},
    )
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_export_empty_path_rejected(client: AsyncClient) -> None:
    resp = await client.post(
        "/api/v1/datasets/docs/export",
        json={"output_path": ""},
    )
    assert resp.status_code == 422


# ---------------------------------------------------------------------------
# Export task status
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_export_status_after_create(client: AsyncClient, mock_lake: MagicMock) -> None:
    resp = await client.post(
        "/api/v1/datasets/docs/export",
        json={"output_path": "output/docs.parquet"},
    )
    body = resp.json()
    task_id = body["task_id"]

    await asyncio.sleep(0.1)

    status_resp = await client.get(f"/api/v1/datasets/docs/export/{task_id}/status")
    assert status_resp.status_code == 200
    status_body = status_resp.json()
    assert status_body["task_id"] == task_id
    assert status_body["status"] in ("completed", "pending", "running")


@pytest.mark.asyncio
async def test_export_status_not_found(client: AsyncClient) -> None:
    resp = await client.get("/api/v1/datasets/docs/export/nonexistent/status")
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Export download
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_export_download_not_found(client: AsyncClient) -> None:
    resp = await client.get("/api/v1/datasets/docs/export/nonexistent/download")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_export_download_before_complete(client: AsyncClient, mock_lake: MagicMock) -> None:
    # Create an export that will never complete (mock without side_effect)
    mock_lake.export.side_effect = asyncio.sleep(10)
    resp = await client.post(
        "/api/v1/datasets/docs/export",
        json={"output_path": "output/docs.parquet"},
    )
    body = resp.json()
    task_id = body["task_id"]

    download_resp = await client.get(f"/api/v1/datasets/docs/export/{task_id}/download")
    assert download_resp.status_code == 400
