"""Tests for query endpoints (OLAP, metadata, Daft)."""

from __future__ import annotations

from dataclasses import dataclass
from unittest.mock import MagicMock

import pytest
from httpx import ASGITransport, AsyncClient
from pyarrow import Table as PaTable

from arrow_lake.api.app import create_app


@dataclass(frozen=True)
class _FakeOlapResult:
    table: PaTable
    row_count: int = 3
    column_count: int = 2
    sql: str = "SELECT count(*) FROM docs"


def _sample_table() -> PaTable:
    import pyarrow as pa
    return pa.table({"id": [1, 2, 3], "count": [10, 20, 30]})


@pytest.fixture
def mock_lake() -> MagicMock:
    lake = MagicMock()
    tbl = _sample_table()
    lake.olap_query.return_value = _FakeOlapResult(table=tbl)
    lake.sql_query.return_value = _FakeOlapResult(table=tbl)

    fake_frame = MagicMock()
    fake_frame.collect.return_value = tbl
    lake.daft_query.return_value = fake_frame
    return lake


@pytest.fixture
async def client(mock_lake: MagicMock) -> AsyncClient:
    app = create_app()
    app.state.lake = mock_lake
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        yield ac


# ---------------------------------------------------------------------------
# OLAP query
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_olap_query_json(client: AsyncClient, mock_lake: MagicMock) -> None:
    resp = await client.post(
        "/api/v1/datasets/docs/query/olap",
        json={"sql": "SELECT count(*) FROM docs", "format": "json"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["success"] is True
    assert body["row_count"] == 3
    assert body["meta"]["sql"] == "SELECT count(*) FROM docs"

    mock_lake.olap_query.assert_called_once_with(
        "docs", "SELECT count(*) FROM docs", max_rows=None
    )


@pytest.mark.asyncio
async def test_olap_query_ipc(client: AsyncClient, mock_lake: MagicMock) -> None:
    resp = await client.post(
        "/api/v1/datasets/docs/query/olap",
        json={"sql": "SELECT 1", "format": "arrow_ipc"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["format"] == "arrow_ipc"
    assert body["data"] is not None


@pytest.mark.asyncio
async def test_olap_query_empty_sql_rejected(client: AsyncClient) -> None:
    resp = await client.post(
        "/api/v1/datasets/docs/query/olap",
        json={"sql": ""},
    )
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_olap_query_with_max_rows(client: AsyncClient, mock_lake: MagicMock) -> None:
    resp = await client.post(
        "/api/v1/datasets/docs/query/olap",
        json={"sql": "SELECT * FROM docs", "max_rows": 100},
    )
    assert resp.status_code == 200
    mock_lake.olap_query.assert_called_once_with(
        "docs", "SELECT * FROM docs", max_rows=100
    )


# ---------------------------------------------------------------------------
# Metadata query
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_metadata_query(client: AsyncClient, mock_lake: MagicMock) -> None:
    resp = await client.post(
        "/api/v1/datasets/docs/query/metadata",
        json={"sql": "SELECT * FROM docs WHERE modality='text'"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["row_count"] == 3

    mock_lake.sql_query.assert_called_once_with(
        "docs", "SELECT * FROM docs WHERE modality='text'", max_rows=None
    )


# ---------------------------------------------------------------------------
# Daft query
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_daft_query_json(client: AsyncClient, mock_lake: MagicMock) -> None:
    resp = await client.post(
        "/api/v1/datasets/docs/query/daft",
        json={"columns": ["id", "text"], "format": "json"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["row_count"] == 3
    assert len(body["rows"]) == 3

    mock_lake.daft_query.assert_called_once_with("docs", columns=["id", "text"])
    mock_lake.daft_query.return_value.collect.assert_called_once()


@pytest.mark.asyncio
async def test_daft_query_all_columns(client: AsyncClient, mock_lake: MagicMock) -> None:
    resp = await client.post(
        "/api/v1/datasets/docs/query/daft",
        json={},
    )
    assert resp.status_code == 200
    mock_lake.daft_query.assert_called_once_with("docs", columns=None)


# ---------------------------------------------------------------------------
# Dataset name validation
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_query_dataset_name_traversal_rejected(client: AsyncClient) -> None:
    resp = await client.post(
        "/api/v1/datasets/../etc/query/olap",
        json={"sql": "SELECT 1"},
    )
    assert resp.status_code in (404, 422)
