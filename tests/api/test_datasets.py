"""Tests for dataset management and ingestion endpoints."""

from __future__ import annotations

from dataclasses import dataclass
from unittest.mock import MagicMock

import pytest
from arrow_lake.api.app import create_app
from arrow_lake.api.models.dataset import IngestResponse
from arrow_lake.exceptions import CatalogError, ErrorCode
from httpx import ASGITransport, AsyncClient


@dataclass(frozen=True)
class _FakeSource:
    path: str
    row_count: int
    file_count: int = 1


@dataclass(frozen=True)
class _FakeReport:
    sources: tuple = ()
    total_rows: int = 0
    total_files: int = 0


@dataclass(frozen=True)
class _FakeEntry:
    name: str
    version: int
    num_rows: int
    num_columns: int = 0
    vector_dim: int | None = None
    has_vector_index: bool = False
    has_fts_index: bool = False
    size_bytes: int = 0
    created_at: str = ""
    updated_at: str = ""


@dataclass(frozen=True)
class _FakeCatalogResult:
    datasets: list = ()
    total: int = 0


@pytest.fixture
def mock_lake_with_catalog() -> MagicMock:
    lake = MagicMock()
    lake.catalog.return_value = _FakeCatalogResult(
        datasets=[
            _FakeEntry(name="documents", version=3, num_rows=1000),
            _FakeEntry(name="images", version=1, num_rows=500),
        ],
        total=2,
    )
    return lake


@pytest.fixture
async def client(mock_lake_with_catalog: MagicMock) -> AsyncClient:
    from arrow_lake.config import ArrowLakeConfig
    config = ArrowLakeConfig()
    config.api.api_key = "test-key"
    config.api.api_key_default_role = "ADMIN"
    app = create_app(config)
    app.state.lake = mock_lake_with_catalog
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
        headers={"X-API-Key": "test-key"},
    ) as ac:
        yield ac


# ---- GET /api/v1/datasets ----

@pytest.mark.asyncio
async def test_list_datasets(client: AsyncClient) -> None:
    resp = await client.get("/api/v1/datasets")
    assert resp.status_code == 200
    body = resp.json()
    assert body["success"] is True
    assert body["total"] == 2
    assert len(body["datasets"]) == 2
    assert body["datasets"][0]["name"] == "documents"
    assert body["datasets"][0]["version"] == 3
    assert body["datasets"][0]["num_rows"] == 1000


@pytest.mark.asyncio
async def test_list_datasets_empty(mock_lake_with_catalog: MagicMock) -> None:
    mock_lake_with_catalog.catalog.return_value = _FakeCatalogResult(datasets=[], total=0)
    from arrow_lake.config import ArrowLakeConfig
    config = ArrowLakeConfig()
    config.api.api_key = "test-key"
    config.api.api_key_default_role = "ADMIN"
    app = create_app(config)
    app.state.lake = mock_lake_with_catalog

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test",
                           headers={"X-API-Key": "test-key"}) as ac:
        resp = await ac.get("/api/v1/datasets")
        assert resp.status_code == 200
        assert resp.json()["total"] == 0


# ---- GET /api/v1/datasets/{name} ----

@pytest.mark.asyncio
async def test_get_dataset_found(client: AsyncClient) -> None:
    resp = await client.get("/api/v1/datasets/documents")
    assert resp.status_code == 200
    body = resp.json()
    assert body["name"] == "documents"
    assert body["num_rows"] == 1000
    assert body["has_kg"] is False  # MagicMock lake → no KA base on disk


@pytest.mark.asyncio
async def test_get_dataset_not_found(client: AsyncClient) -> None:
    resp = await client.get("/api/v1/datasets/nonexistent")
    assert resp.status_code == 404
    assert "not found" in resp.json()["message"]


def test_dataset_has_kg_detects_built_kg(tmp_path) -> None:
    """_dataset_has_kg True iff KA dump exists on disk (O(1) file check)."""
    from arrow_lake.api.routers.datasets import _dataset_has_kg
    from arrow_lake.knowledge_graph._naming import artifact_key_for

    name = "wuhu_report"
    lake = MagicMock()
    lake.config.hugegraph.he_ka_base_dir = str(tmp_path)

    assert _dataset_has_kg(lake, name) is False  # no KA dump yet

    key = artifact_key_for(name)
    (tmp_path / key / "ka").mkdir(parents=True)
    (tmp_path / key / "ka" / "data.json").write_text("{}")
    assert _dataset_has_kg(lake, name) is True


def test_dataset_has_kg_false_when_kg_disabled() -> None:
    """KG disabled (hugegraph None) → False, never raises."""
    from arrow_lake.api.routers.datasets import _dataset_has_kg

    lake = MagicMock()
    lake.config.hugegraph = None
    assert _dataset_has_kg(lake, "any") is False


# ---- POST /api/v1/datasets/{name}/ingest ----

@pytest.mark.asyncio
async def test_ingest_files(client: AsyncClient, mock_lake_with_catalog: MagicMock) -> None:
    mock_lake_with_catalog.ingest.return_value = _FakeReport(
        sources=(
            _FakeSource(path="data.csv", row_count=50, file_count=1),
        ),
        total_rows=50,
        total_files=1,
    )

    resp = await client.post(
        "/api/v1/datasets/test/ingest",
        json={"file_paths": ["data.csv", "data2.json"]},
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["success"] is True
    assert body["total_rows"] == 50
    assert body["total_files"] == 1
    assert len(body["sources"]) == 1
    assert body["sources"][0]["path"] == "data.csv"

    # Verify Lake.ingest was called with correct args
    mock_lake_with_catalog.ingest.assert_called_once_with("test", ["data.csv", "data2.json"], transforms=None, actor="api-key")


# ---- POST /api/v1/datasets/{name}/ingest/http ----

@pytest.mark.asyncio
async def test_ingest_http(client: AsyncClient, mock_lake_with_catalog: MagicMock) -> None:
    mock_lake_with_catalog.ingest_http.return_value = _FakeReport(
        sources=(
            _FakeSource(path="https://example.com/data.json", row_count=100, file_count=1),
        ),
        total_rows=100,
        total_files=1,
    )

    resp = await client.post(
        "/api/v1/datasets/test/ingest/http",
        json={"urls": ["https://example.com/data.json"]},
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["total_rows"] == 100
    assert body["sources"][0]["path"] == "https://example.com/data.json"

    mock_lake_with_catalog.ingest_http.assert_called_once_with(
        "test", ["https://example.com/data.json"], actor="api-key"
    )


# ---- DELETE /api/v1/datasets/{name} ----

@pytest.mark.asyncio
async def test_delete_dataset(client: AsyncClient, mock_lake_with_catalog: MagicMock) -> None:
    resp = await client.delete("/api/v1/datasets/documents")
    assert resp.status_code == 200
    assert "deleted" in resp.json()["message"]
    mock_lake_with_catalog.delete_dataset.assert_called_once_with(
        "documents", actor="api-key", cascade=True, table=None
    )


@pytest.mark.asyncio
async def test_delete_dataset_cascade_false_query(
    client: AsyncClient, mock_lake_with_catalog: MagicMock
) -> None:
    """?cascade=false opts out of derived-asset reclamation (table-only delete)."""
    resp = await client.delete("/api/v1/datasets/documents?cascade=false")
    assert resp.status_code == 200
    mock_lake_with_catalog.delete_dataset.assert_called_once_with(
        "documents", actor="api-key", cascade=False, table=None
    )


@pytest.mark.asyncio
async def test_delete_dataset_not_found(mock_lake_with_catalog: MagicMock) -> None:
    from arrow_lake.config import ArrowLakeConfig
    mock_lake_with_catalog.delete_dataset.side_effect = CatalogError(
        error_code=ErrorCode.CATALOG_DATASET_NOT_FOUND,
        message="Dataset 'nonexistent' not found",
    )
    config = ArrowLakeConfig()
    config.api.api_key = "test-key"
    config.api.api_key_default_role = "ADMIN"
    app = create_app(config)
    app.state.lake = mock_lake_with_catalog

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test",
                           headers={"X-API-Key": "test-key"}) as ac:
        resp = await ac.delete("/api/v1/datasets/nonexistent")
        assert resp.status_code == 404


# ---- IngestResponse.from_report ----

def test_ingest_response_from_report() -> None:
    report = _FakeReport(
        sources=(
            _FakeSource(path="a.csv", row_count=10),
            _FakeSource(path="b.csv", row_count=20),
        ),
        total_rows=30,
        total_files=2,
    )
    resp = IngestResponse.from_report(report)
    assert resp.total_rows == 30
    assert resp.total_files == 2
    assert len(resp.sources) == 2
    assert resp.sources[1].path == "b.csv"


# ---- Input validation ----

@pytest.mark.asyncio
async def test_dataset_name_with_traversal_rejected(client: AsyncClient) -> None:
    resp = await client.post(
        "/api/v1/datasets/../etc/ingest",
        json={"file_paths": ["data.csv"]},
    )
    # FastAPI path regex rejection returns 404 (no matching route)
    assert resp.status_code in (404, 422)


@pytest.mark.asyncio
async def test_ingest_file_path_traversal_rejected(client: AsyncClient) -> None:
    resp = await client.post(
        "/api/v1/datasets/test/ingest",
        json={"file_paths": ["../../etc/passwd"]},
    )
    assert resp.status_code == 422


# ---- POST /api/v1/datasets/{name}/ingest/images ----

@pytest.mark.asyncio
async def test_ingest_images(client: AsyncClient, mock_lake_with_catalog: MagicMock) -> None:
    mock_lake_with_catalog.ingest_images.return_value = _FakeReport(
        sources=(_FakeSource(path="photo.jpg", row_count=5, file_count=1),),
        total_rows=5,
        total_files=1,
    )

    resp = await client.post(
        "/api/v1/datasets/test/ingest/images",
        json={"file_paths": ["photo.jpg", "image.png"]},
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["success"] is True
    assert body["total_rows"] == 5

    mock_lake_with_catalog.ingest_images.assert_called_once_with(
        "test", ["photo.jpg", "image.png"], actor="api-key"
    )


@pytest.mark.asyncio
async def test_ingest_images_path_traversal_rejected(client: AsyncClient) -> None:
    resp = await client.post(
        "/api/v1/datasets/test/ingest/images",
        json={"file_paths": ["../../etc/passwd"]},
    )
    assert resp.status_code == 422


# ---- POST /api/v1/datasets/{name}/ingest/videos ----

@pytest.mark.asyncio
async def test_ingest_videos(client: AsyncClient, mock_lake_with_catalog: MagicMock) -> None:
    mock_lake_with_catalog.ingest_videos.return_value = _FakeReport(
        sources=(_FakeSource(path="video.mp4", row_count=3, file_count=1),),
        total_rows=3,
        total_files=1,
    )

    resp = await client.post(
        "/api/v1/datasets/test/ingest/videos",
        json={"file_paths": ["video.mp4"]},
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["total_rows"] == 3

    mock_lake_with_catalog.ingest_videos.assert_called_once_with("test", ["video.mp4"], actor="api-key")


# ---- POST /api/v1/datasets/{name}/ingest/mixed ----

@pytest.mark.asyncio
async def test_ingest_mixed(client: AsyncClient, mock_lake_with_catalog: MagicMock) -> None:
    mock_lake_with_catalog.ingest_mixed.return_value = _FakeReport(
        sources=(
            _FakeSource(path="data.csv", row_count=10, file_count=1),
            _FakeSource(path="photo.jpg", row_count=5, file_count=1),
        ),
        total_rows=15,
        total_files=2,
    )

    resp = await client.post(
        "/api/v1/datasets/test/ingest/mixed",
        json={
            "sources": {
                "files": ["data.csv"],
                "images": ["photo.jpg"],
            }
        },
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["total_rows"] == 15
    assert body["total_files"] == 2

    mock_lake_with_catalog.ingest_mixed.assert_called_once_with("test", {
        "files": ["data.csv"],
        "images": ["photo.jpg"],
    }, actor="api-key")
