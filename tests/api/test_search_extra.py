"""Tests for search endpoint extras: where filters, ACL, extra params."""

from __future__ import annotations

from dataclasses import dataclass
from unittest.mock import MagicMock

import pyarrow as pa
import pytest
from arrow_lake.api.app import create_app
from arrow_lake.api.rbac import DatasetACL, PermissionChecker
from arrow_lake.config import ArrowLakeConfig
from httpx import ASGITransport, AsyncClient


@dataclass(frozen=True)
class _FakeVectorResult:
    table: object
    row_count: int = 2
    query_vector_dim: int = 64
    metric: str = "cosine"
    top_k: int = 10
    max_distance: float = 0.5


@dataclass(frozen=True)
class _FakeFtsResult:
    table: object
    row_count: int = 2
    query: str = "search terms"
    top_k: int = 10
    fts_column: str = "text_content"
    max_score: float = 5.0


@dataclass(frozen=True)
class _FakeHybridResult:
    table: object
    row_count: int = 2
    query_text: str = "hybrid query"
    query_vector_dim: int = 64
    top_k: int = 10
    rrf_k: int = 60
    max_rrf_score: float = 0.02


@dataclass(frozen=True)
class _FakeFacetCount:
    name: str
    value: str
    count: int


@dataclass(frozen=True)
class _FakeFacetedResult:
    table: object
    row_count: int = 2
    facets: tuple = ()
    total_facets: int = 0
    query_vector_dim: int = 64
    top_k: int = 10


@dataclass(frozen=True)
class _FakeEnsembleResult:
    table: object
    row_count: int = 2
    columns_searched: tuple = ()
    fusion_method: str = "rrf"
    top_k: int = 10
    query_vector_dim: int = 64


def _sample_table() -> pa.Table:
    return pa.table({"id": [1, 2], "text": ["a", "b"]})


@pytest.fixture
def mock_lake() -> MagicMock:
    lake = MagicMock()
    tbl = _sample_table()
    lake.search.return_value = _FakeVectorResult(table=tbl)
    lake.text_search.return_value = _FakeFtsResult(table=tbl)
    lake.hybrid_search.return_value = _FakeHybridResult(table=tbl)
    lake.faceted_search.return_value = _FakeFacetedResult(
        table=tbl,
        facets=(_FakeFacetCount(name="tag", value="ml", count=5),),
        total_facets=1,
    )
    lake.ensemble_search.return_value = _FakeEnsembleResult(
        table=tbl,
        columns_searched=("emb1",),
    )
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
# Vector search with where/nprobes/vector_column
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_vector_search_with_where(client: AsyncClient, mock_lake: MagicMock) -> None:
    resp = await client.post(
        "/api/v1/datasets/docs/search/vector",
        json={"query_vector": [0.1] * 64, "where": "region == US"},
    )
    assert resp.status_code == 200
    call_kwargs = mock_lake.search.call_args
    assert call_kwargs[1]["where"] == "region == US"


@pytest.mark.asyncio
async def test_vector_search_with_nprobes(client: AsyncClient, mock_lake: MagicMock) -> None:
    resp = await client.post(
        "/api/v1/datasets/docs/search/vector",
        json={"query_vector": [0.1] * 64, "nprobes": 20},
    )
    assert resp.status_code == 200
    call_kwargs = mock_lake.search.call_args
    assert call_kwargs[1]["nprobes"] == 20


@pytest.mark.asyncio
async def test_vector_search_with_vector_column(client: AsyncClient, mock_lake: MagicMock) -> None:
    resp = await client.post(
        "/api/v1/datasets/docs/search/vector",
        json={"query_vector": [0.1] * 64, "vector_column": "text_embedding"},
    )
    assert resp.status_code == 200
    call_kwargs = mock_lake.search.call_args
    assert call_kwargs[1]["vector_column"] == "text_embedding"


# ---------------------------------------------------------------------------
# FTS with extra params
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_fts_with_fts_column(client: AsyncClient, mock_lake: MagicMock) -> None:
    resp = await client.post(
        "/api/v1/datasets/docs/search/fts",
        json={"query": "test", "fts_column": "title"},
    )
    assert resp.status_code == 200
    call_kwargs = mock_lake.text_search.call_args
    assert call_kwargs[1]["fts_column"] == "title"


@pytest.mark.asyncio
async def test_fts_with_offset(client: AsyncClient, mock_lake: MagicMock) -> None:
    resp = await client.post(
        "/api/v1/datasets/docs/search/fts",
        json={"query": "test", "offset": 10},
    )
    assert resp.status_code == 200
    call_kwargs = mock_lake.text_search.call_args
    assert call_kwargs[1]["offset"] == 10


@pytest.mark.asyncio
async def test_fts_with_where(client: AsyncClient, mock_lake: MagicMock) -> None:
    resp = await client.post(
        "/api/v1/datasets/docs/search/fts",
        json={"query": "test", "where": "year > 2020"},
    )
    assert resp.status_code == 200
    call_kwargs = mock_lake.text_search.call_args
    assert call_kwargs[1]["where"] == "year > 2020"


# ---------------------------------------------------------------------------
# Hybrid search with where
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_hybrid_search_with_where(client: AsyncClient, mock_lake: MagicMock) -> None:
    resp = await client.post(
        "/api/v1/datasets/docs/search/hybrid",
        json={
            "query_vector": [0.1] * 64,
            "query_text": "test",
            "where": "status == active",
        },
    )
    assert resp.status_code == 200
    call_kwargs = mock_lake.hybrid_search.call_args
    assert call_kwargs[1]["where"] == "status == active"


# ---------------------------------------------------------------------------
# Faceted search with where and weights
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_faceted_search_with_where(client: AsyncClient, mock_lake: MagicMock) -> None:
    resp = await client.post(
        "/api/v1/datasets/docs/search/faceted",
        json={"query_vector": [0.1] * 64, "facets": ["tag"], "where": "year > 2023"},
    )
    assert resp.status_code == 200
    call_kwargs = mock_lake.faceted_search.call_args
    assert call_kwargs[1]["where"] == "year > 2023"


# ---------------------------------------------------------------------------
# Ensemble search with where and weights
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_ensemble_search_with_weights(client: AsyncClient, mock_lake: MagicMock) -> None:
    resp = await client.post(
        "/api/v1/datasets/docs/search/ensemble",
        json={
            "query_vector": [0.1] * 64,
            "columns": ["emb1", "emb2"],
            "weights": {"emb1": 0.7, "emb2": 0.3},
            "where": "quality > 0.5",
        },
    )
    assert resp.status_code == 200
    call_kwargs = mock_lake.ensemble_search.call_args
    assert call_kwargs[1]["weights"] == {"emb1": 0.7, "emb2": 0.3}
    assert call_kwargs[1]["where"] == "quality > 0.5"


# ---------------------------------------------------------------------------
# ACL filter on search results
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_vector_search_acl_column_filter(mock_lake: MagicMock) -> None:
    checker = PermissionChecker()
    checker.set_acl(DatasetACL(
        dataset="docs", role="viewer",
        visible_columns=frozenset({"id", "text"}),
    ))

    tbl = pa.table({"id": [1], "text": ["hello"], "secret": ["s3cr3t"]})
    mock_lake.search.return_value = _FakeVectorResult(table=tbl)

    config = ArrowLakeConfig()
    config.api.api_key = "test-api-key"
    config.api.api_key_default_role = "VIEWER"
    config.api.docs_enabled = False
    app = create_app(config=config)
    app.state.lake = mock_lake
    app.state.checker = checker

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
        headers={"X-API-Key": "test-api-key"},
    ) as ac:
        resp = await ac.post(
            "/api/v1/datasets/docs/search/vector",
            json={"query_vector": [0.1] * 64, "format": "json"},
        )
    assert resp.status_code == 200
    body = resp.json()
    # ACL should filter out the "secret" column for viewer role
    assert "secret" not in body["rows"][0]
