"""Tests for search endpoints (vector, FTS, hybrid, faceted, ensemble)."""

from __future__ import annotations

from dataclasses import dataclass
from unittest.mock import MagicMock

import pytest
from httpx import ASGITransport, AsyncClient
from pyarrow import Table as PaTable

from arrow_lake.api.app import create_app


# ---------------------------------------------------------------------------
# Fake result types matching SDK dataclass shapes
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class _FakeFacetCount:
    name: str
    value: str
    count: int


@dataclass(frozen=True)
class _FakeVectorResult:
    table: PaTable
    row_count: int = 3
    query_vector_dim: int = 128
    metric: str = "cosine"
    top_k: int = 10
    max_distance: float = 0.85


@dataclass(frozen=True)
class _FakeFtsResult:
    table: PaTable
    row_count: int = 2
    query: str = "test query"
    top_k: int = 10
    fts_column: str = "text_content"
    max_score: float = 3.2


@dataclass(frozen=True)
class _FakeHybridResult:
    table: PaTable
    row_count: int = 3
    query_text: str = "hybrid query"
    query_vector_dim: int = 128
    top_k: int = 10
    rrf_k: int = 60
    max_rrf_score: float = 0.025


@dataclass(frozen=True)
class _FakeFacetedResult:
    table: PaTable
    row_count: int = 3
    facets: tuple = ()
    total_facets: int = 0
    query_vector_dim: int = 128
    top_k: int = 10


@dataclass(frozen=True)
class _FakeEnsembleResult:
    table: PaTable
    row_count: int = 3
    columns_searched: tuple = ()
    fusion_method: str = "rrf"
    top_k: int = 10
    query_vector_dim: int = 128


def _sample_table() -> PaTable:
    """Create a minimal table for search results."""
    import pyarrow as pa
    return pa.table({"id": [1, 2, 3], "text": ["a", "b", "c"], "_distance": [0.1, 0.2, 0.3]})


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def mock_lake() -> MagicMock:
    lake = MagicMock()
    tbl = _sample_table()
    lake.search.return_value = _FakeVectorResult(table=tbl)
    lake.text_search.return_value = _FakeFtsResult(table=tbl)
    lake.hybrid_search.return_value = _FakeHybridResult(table=tbl)
    lake.faceted_search.return_value = _FakeFacetedResult(
        table=tbl,
        facets=(
            _FakeFacetCount(name="modality", value="text", count=10),
            _FakeFacetCount(name="modality", value="image", count=5),
        ),
        total_facets=2,
    )
    lake.ensemble_search.return_value = _FakeEnsembleResult(
        table=tbl,
        columns_searched=("text_embedding", "image_embedding"),
    )
    return lake


@pytest.fixture
async def client(mock_lake: MagicMock) -> AsyncClient:
    app = create_app()
    app.state.lake = mock_lake
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        yield ac


# ---------------------------------------------------------------------------
# Vector search
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_vector_search_json(client: AsyncClient, mock_lake: MagicMock) -> None:
    resp = await client.post(
        "/api/v1/datasets/docs/search/vector",
        json={"query_vector": [0.1] * 128, "top_k": 5, "format": "json"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["success"] is True
    assert body["row_count"] == 3
    assert body["format"] == "json"
    assert len(body["rows"]) == 3
    assert body["meta"]["query_vector_dim"] == 128
    assert body["meta"]["metric"] == "cosine"

    mock_lake.search.assert_called_once()
    call_kwargs = mock_lake.search.call_args
    assert call_kwargs[0][0] == "docs"
    assert call_kwargs[1]["top_k"] == 5


@pytest.mark.asyncio
async def test_vector_search_ipc(client: AsyncClient, mock_lake: MagicMock) -> None:
    resp = await client.post(
        "/api/v1/datasets/docs/search/vector",
        json={"query_vector": [0.1] * 128, "format": "arrow_ipc"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["format"] == "arrow_ipc"
    assert body["data"] is not None
    assert body["rows"] is None


@pytest.mark.asyncio
async def test_vector_search_empty_vector_rejected(client: AsyncClient) -> None:
    resp = await client.post(
        "/api/v1/datasets/docs/search/vector",
        json={"query_vector": [], "format": "json"},
    )
    assert resp.status_code == 422


# ---------------------------------------------------------------------------
# FTS
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_fts_search_json(client: AsyncClient, mock_lake: MagicMock) -> None:
    resp = await client.post(
        "/api/v1/datasets/docs/search/fts",
        json={"query": "machine learning", "format": "json"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["row_count"] == 3
    assert body["meta"]["query"] == "test query"  # from mock
    assert body["meta"]["max_score"] == 3.2

    mock_lake.text_search.assert_called_once()
    call_kwargs = mock_lake.text_search.call_args
    assert call_kwargs[0][1] == "machine learning"


@pytest.mark.asyncio
async def test_fts_search_empty_query_rejected(client: AsyncClient) -> None:
    resp = await client.post(
        "/api/v1/datasets/docs/search/fts",
        json={"query": "", "format": "json"},
    )
    assert resp.status_code == 422


# ---------------------------------------------------------------------------
# Hybrid search
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_hybrid_search(client: AsyncClient, mock_lake: MagicMock) -> None:
    resp = await client.post(
        "/api/v1/datasets/docs/search/hybrid",
        json={
            "query_vector": [0.1] * 128,
            "query_text": "search terms",
            "format": "json",
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["row_count"] == 3
    assert body["meta"]["rrf_k"] == 60
    assert body["meta"]["query_text"] == "hybrid query"  # from mock

    mock_lake.hybrid_search.assert_called_once()


# ---------------------------------------------------------------------------
# Faceted search
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_faceted_search(client: AsyncClient, mock_lake: MagicMock) -> None:
    resp = await client.post(
        "/api/v1/datasets/docs/search/faceted",
        json={"query_vector": [0.1] * 128, "facets": ["modality"], "format": "json"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["row_count"] == 3
    assert body["total_facets"] == 2
    assert len(body["facets"]) == 2
    assert body["facets"][0]["name"] == "modality"
    assert body["facets"][0]["count"] == 10

    mock_lake.faceted_search.assert_called_once()
    call_kwargs = mock_lake.faceted_search.call_args
    assert call_kwargs[1]["facets"] == ["modality"]


# ---------------------------------------------------------------------------
# Ensemble search
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_ensemble_search(client: AsyncClient, mock_lake: MagicMock) -> None:
    resp = await client.post(
        "/api/v1/datasets/docs/search/ensemble",
        json={
            "query_vector": [0.1] * 128,
            "columns": ["text_embedding", "image_embedding"],
            "format": "json",
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["row_count"] == 3
    assert body["meta"]["columns_searched"] == ["text_embedding", "image_embedding"]
    assert body["meta"]["fusion_method"] == "rrf"

    mock_lake.ensemble_search.assert_called_once()


# ---------------------------------------------------------------------------
# Dataset name validation
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_search_dataset_name_traversal_rejected(client: AsyncClient) -> None:
    resp = await client.post(
        "/api/v1/datasets/../etc/search/vector",
        json={"query_vector": [0.1]},
    )
    assert resp.status_code in (404, 422)
