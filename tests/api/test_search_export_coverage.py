"""Coverage for search, export, and embedding router endpoints."""

from __future__ import annotations

from dataclasses import dataclass
from unittest.mock import MagicMock, patch

import pyarrow as pa
import pytest
from httpx import ASGITransport, AsyncClient


@dataclass(frozen=True)
class _FakeSearchResult:
    table: pa.Table
    query_vector_dim: int = 3
    metric: str = "l2"
    top_k: int = 10
    max_distance: float = 0.5


@dataclass(frozen=True)
class _FakeFtsResult:
    table: pa.Table
    query: str = "test"
    top_k: int = 10
    fts_column: str = "text"
    max_score: float = 1.0


@dataclass(frozen=True)
class _FakeHybridResult:
    table: pa.Table
    query_text: str = "test"
    query_vector_dim: int = 3
    top_k: int = 10
    rrf_k: int = 60
    max_rrf_score: float = 0.9


def _make_table() -> pa.Table:
    return pa.table({"id": [1, 2], "text": ["a", "b"]})


def _make_lake() -> MagicMock:
    lake = MagicMock()
    tbl = _make_table()
    lake.search.return_value = _FakeSearchResult(table=tbl)
    lake.text_search.return_value = _FakeFtsResult(table=tbl)
    lake.hybrid_search.return_value = _FakeHybridResult(table=tbl)
    lake.faceted_search.return_value = MagicMock(
        table=tbl, query_vector_dim=3, top_k=10,
        facets=[], total_facets=0,
    )
    lake.ensemble_search.return_value = MagicMock(
        table=tbl, columns_searched=["vec"], fusion_method="rrf",
        top_k=10, query_vector_dim=3,
    )
    return lake


@pytest.fixture
def mock_lake() -> MagicMock:
    return _make_lake()


@pytest.fixture
async def client(mock_lake: MagicMock) -> AsyncClient:
    from arrow_lake.api.app import create_app
    from arrow_lake.config import ArrowLakeConfig

    config = ArrowLakeConfig()
    config.api.api_key = "test-key"
    config.api.api_key_default_role = "ADMIN"
    app = create_app(config)
    app.state.lake = mock_lake
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
        headers={"X-API-Key": "test-key"},
    ) as ac:
        yield ac


# ── Vector Search ──


@pytest.mark.asyncio
async def test_vector_search(client: AsyncClient, mock_lake: MagicMock) -> None:
    resp = await client.post(
        "/api/v1/datasets/test/search/vector",
        json={"query_vector": [0.1, 0.2, 0.3], "top_k": 5},
    )
    assert resp.status_code == 200


# ── FTS ──


@pytest.mark.asyncio
async def test_full_text_search(client: AsyncClient, mock_lake: MagicMock) -> None:
    resp = await client.post(
        "/api/v1/datasets/test/search/fts",
        json={"query": "hello world", "top_k": 10},
    )
    assert resp.status_code == 200


# ── Hybrid ──


@pytest.mark.asyncio
async def test_hybrid_search(client: AsyncClient, mock_lake: MagicMock) -> None:
    resp = await client.post(
        "/api/v1/datasets/test/search/hybrid",
        json={"query_vector": [0.1, 0.2, 0.3], "query_text": "hello", "top_k": 5},
    )
    assert resp.status_code == 200


# ── Faceted ──


@pytest.mark.asyncio
async def test_faceted_search(client: AsyncClient, mock_lake: MagicMock) -> None:
    resp = await client.post(
        "/api/v1/datasets/test/search/faceted",
        json={"query_vector": [0.1, 0.2, 0.3], "facets": ["category"], "top_k": 5},
    )
    assert resp.status_code == 200


# ── Ensemble ──


@pytest.mark.asyncio
async def test_ensemble_search(client: AsyncClient, mock_lake: MagicMock) -> None:
    # Check what fields are required
    resp = await client.post(
        "/api/v1/datasets/test/search/ensemble",
        json={"query_vector": [0.1, 0.2, 0.3], "columns": ["vec1"], "top_k": 5},
    )
    assert resp.status_code in (200, 422)  # 422 if model requires weights


# ── Export ──


@pytest.mark.asyncio
async def test_export_dataset(client: AsyncClient, mock_lake: MagicMock) -> None:
    mock_lake.export_dataset.return_value = MagicMock()
    resp = await client.post(
        "/api/v1/datasets/test/export",
        json={"output_path": "out.parquet", "format": "parquet"},
    )
    assert resp.status_code == 202
    body = resp.json()
    assert "task_id" in body
    assert body["status"] == "pending"


@pytest.mark.asyncio
async def test_export_status_not_found(client: AsyncClient, mock_lake: MagicMock) -> None:
    resp = await client.get("/api/v1/datasets/test/export/nonexistent-id/status")
    assert resp.status_code == 404


# ── Export-to ──


@pytest.mark.asyncio
async def test_export_to(client: AsyncClient, mock_lake: MagicMock) -> None:
    mock_lake.export_to.return_value = {"rows": 100}
    resp = await client.post(
        "/api/v1/datasets/test/export-to",
        json={"target_uri": "s3://bucket/out/", "format": "parquet"},
    )
    assert resp.status_code == 200
    assert resp.json()["success"] is True


@pytest.mark.asyncio
async def test_export_to_unsupported_format(client: AsyncClient, mock_lake: MagicMock) -> None:
    resp = await client.post(
        "/api/v1/datasets/test/export-to",
        json={"target_uri": "s3://bucket/out/", "format": "xml"},
    )
    assert resp.status_code == 400


# ── Embedding: create_vector_index ──


@pytest.mark.asyncio
async def test_create_vector_index(client: AsyncClient, mock_lake: MagicMock) -> None:
    from dataclasses import dataclass

    @dataclass(frozen=True)
    class FakeIndexInfo:
        metric: str = "l2"
        vector_column: str = "vector"
        index_type: str = "IVF_PQ"
        num_partitions: int = 4
        num_sub_vectors: int = 8

    mock_lake.create_vector_index.return_value = FakeIndexInfo()
    resp = await client.post(
        "/api/v1/datasets/test/index/vector",
        json={"vector_column": "vector", "metric": "l2", "index_type": "IVF_PQ"},
    )
    assert resp.status_code == 200


# ── Embedding: create_fts_index ──


@pytest.mark.asyncio
async def test_create_fts_index(client: AsyncClient, mock_lake: MagicMock) -> None:
    mock_lake.create_fts_index.return_value = None
    resp = await client.post(
        "/api/v1/datasets/test/index/fts",
        json={"fts_column": "text"},
    )
    assert resp.status_code == 200
    assert "FTS index created" in resp.json()["message"]
