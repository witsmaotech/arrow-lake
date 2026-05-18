"""Tests for query endpoints (OLAP, metadata, Daft)."""

from __future__ import annotations

from dataclasses import dataclass
from unittest.mock import MagicMock, PropertyMock

import pytest
from arrow_lake.api.app import create_app
from arrow_lake.config import ArrowLakeConfig
from httpx import ASGITransport, AsyncClient
from pyarrow import Table as PaTable


@dataclass(frozen=True)
class _FakeOlapResult:
    table: PaTable
    row_count: int = 3
    column_count: int = 2
    sql: str = "SELECT count(*) FROM docs"


def _sample_table() -> PaTable:
    import pyarrow as pa
    return pa.table({"id": [1, 2, 3], "count": [10, 20, 30]})


def _chainable_frame(final_table: PaTable) -> MagicMock:
    """Create a mock LazyDaftFrame that supports chaining every operation."""
    frame = MagicMock()
    frame.collect.return_value = final_table
    frame.check_feasibility.return_value = []
    frame.column_names = ["id", "count"]
    for method in (
        "select", "filter", "sort", "limit", "offset", "distinct",
        "exclude", "drop_null", "fill_null", "with_column", "with_columns",
        "sample", "sql", "explode",
    ):
        getattr(frame, method).return_value = frame
    grouped = MagicMock()
    for agg in ("sum", "mean", "count", "min", "max", "stddev", "var", "agg"):
        getattr(grouped, agg).return_value = frame
    frame.groupby.return_value = grouped
    frame.pivot.return_value = frame
    return frame


@pytest.fixture
def mock_lake() -> MagicMock:
    lake = MagicMock()
    tbl = _sample_table()
    lake.olap_query.return_value = _FakeOlapResult(table=tbl)
    lake.sql_query.return_value = _FakeOlapResult(table=tbl)
    lake.daft_query.return_value = _chainable_frame(tbl)
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
# Daft query — basic
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_daft_query_basic(client: AsyncClient, mock_lake: MagicMock) -> None:
    resp = await client.post(
        "/api/v1/datasets/docs/query/daft",
        json={},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["success"] is True
    assert body["row_count"] == 3
    mock_lake.daft_query.assert_called_once_with("docs")


@pytest.mark.asyncio
async def test_daft_query_with_columns(client: AsyncClient, mock_lake: MagicMock) -> None:
    resp = await client.post(
        "/api/v1/datasets/docs/query/daft",
        json={"columns": ["id", "text"]},
    )
    assert resp.status_code == 200
    frame = mock_lake.daft_query.return_value
    frame.select.assert_called_once_with("id", "text")


@pytest.mark.asyncio
async def test_daft_query_with_sort(client: AsyncClient, mock_lake: MagicMock) -> None:
    resp = await client.post(
        "/api/v1/datasets/docs/query/daft",
        json={"sort": {"column": "count", "desc": True}},
    )
    assert resp.status_code == 200
    frame = mock_lake.daft_query.return_value
    frame.sort.assert_called_once_with("count", desc=True)


@pytest.mark.asyncio
async def test_daft_query_with_filter(client: AsyncClient, mock_lake: MagicMock) -> None:
    resp = await client.post(
        "/api/v1/datasets/docs/query/daft",
        json={"filters": [{"column": "count", "op": "gt", "value": 15}]},
    )
    assert resp.status_code == 200
    frame = mock_lake.daft_query.return_value
    frame.filter.assert_called_once()


@pytest.mark.asyncio
async def test_daft_query_with_groupby(client: AsyncClient, mock_lake: MagicMock) -> None:
    resp = await client.post(
        "/api/v1/datasets/docs/query/daft",
        json={"groupby": {"columns": ["id"], "agg": "mean"}},
    )
    assert resp.status_code == 200
    frame = mock_lake.daft_query.return_value
    frame.groupby.assert_called_once_with("id")
    grouped = frame.groupby.return_value
    grouped.mean.assert_called_once()


@pytest.mark.asyncio
async def test_daft_query_with_sql(client: AsyncClient, mock_lake: MagicMock) -> None:
    resp = await client.post(
        "/api/v1/datasets/docs/query/daft",
        json={"sql": {"query": "SELECT * FROM self LIMIT 10"}},
    )
    assert resp.status_code == 200
    frame = mock_lake.daft_query.return_value
    frame.sql.assert_called_once_with("SELECT * FROM self LIMIT 10")


@pytest.mark.asyncio
async def test_daft_query_sql_blocked(client: AsyncClient, mock_lake: MagicMock) -> None:
    resp = await client.post(
        "/api/v1/datasets/docs/query/daft",
        json={"sql": {"query": "DROP TABLE self"}},
    )
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_daft_query_with_pivot(client: AsyncClient, mock_lake: MagicMock) -> None:
    resp = await client.post(
        "/api/v1/datasets/docs/query/daft",
        json={"pivot": {
            "group_by": "id",
            "pivot_col": "count",
            "value_col": "count",
            "agg_fn": "sum",
        }},
    )
    assert resp.status_code == 200
    frame = mock_lake.daft_query.return_value
    frame.pivot.assert_called_once_with(
        group_by="id", pivot_col="count", value_col="count", agg_fn="sum",
    )


@pytest.mark.asyncio
async def test_daft_query_with_explode(client: AsyncClient, mock_lake: MagicMock) -> None:
    resp = await client.post(
        "/api/v1/datasets/docs/query/daft",
        json={"explode": {"columns": ["tags"]}},
    )
    assert resp.status_code == 200
    frame = mock_lake.daft_query.return_value
    frame.explode.assert_called_once_with("tags")


@pytest.mark.asyncio
async def test_daft_query_with_sample(client: AsyncClient, mock_lake: MagicMock) -> None:
    resp = await client.post(
        "/api/v1/datasets/docs/query/daft",
        json={"sample": {"fraction": 0.5, "seed": 42}},
    )
    assert resp.status_code == 200
    frame = mock_lake.daft_query.return_value
    frame.sample.assert_called_once_with(fraction=0.5, size=None, seed=42)


@pytest.mark.asyncio
async def test_daft_query_with_distinct(client: AsyncClient, mock_lake: MagicMock) -> None:
    resp = await client.post(
        "/api/v1/datasets/docs/query/daft",
        json={"distinct": True},
    )
    assert resp.status_code == 200
    frame = mock_lake.daft_query.return_value
    frame.distinct.assert_called_once_with()


@pytest.mark.asyncio
async def test_daft_query_with_offset(client: AsyncClient, mock_lake: MagicMock) -> None:
    resp = await client.post(
        "/api/v1/datasets/docs/query/daft",
        json={"offset": 10, "limit": 5},
    )
    assert resp.status_code == 200
    frame = mock_lake.daft_query.return_value
    frame.offset.assert_called_once_with(10)
    frame.limit.assert_called_once_with(5)


@pytest.mark.asyncio
async def test_daft_query_with_max_rows(client: AsyncClient, mock_lake: MagicMock) -> None:
    resp = await client.post(
        "/api/v1/datasets/docs/query/daft",
        json={"max_rows": 50},
    )
    assert resp.status_code == 200
    frame = mock_lake.daft_query.return_value
    frame.collect.assert_called_once_with(max_rows=50)


@pytest.mark.asyncio
async def test_daft_query_ipc_format(client: AsyncClient, mock_lake: MagicMock) -> None:
    resp = await client.post(
        "/api/v1/datasets/docs/query/daft",
        json={"format": "arrow_ipc"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["format"] == "arrow_ipc"
    assert body["data"] is not None


@pytest.mark.asyncio
async def test_daft_query_join_not_implemented(client: AsyncClient, mock_lake: MagicMock) -> None:
    resp = await client.post(
        "/api/v1/datasets/docs/query/daft",
        json={"join": {"dataset": "other", "on": "id", "how": "inner"}},
    )
    assert resp.status_code in (500, 501)


@pytest.mark.asyncio
async def test_daft_query_chain_sort_filter_groupby(client: AsyncClient, mock_lake: MagicMock) -> None:
    resp = await client.post(
        "/api/v1/datasets/docs/query/daft",
        json={
            "sort": {"column": "count"},
            "filters": [{"column": "count", "op": "gte", "value": 20}],
            "groupby": {"columns": ["id"], "agg": "sum"},
        },
    )
    assert resp.status_code == 200
    frame = mock_lake.daft_query.return_value
    frame.sort.assert_called_once()
    frame.filter.assert_called_once()
    frame.groupby.assert_called_once()


@pytest.mark.asyncio
async def test_daft_query_includes_check_feasibility(client: AsyncClient, mock_lake: MagicMock) -> None:
    resp = await client.post(
        "/api/v1/datasets/docs/query/daft",
        json={},
    )
    assert resp.status_code == 200
    frame = mock_lake.daft_query.return_value
    frame.check_feasibility.assert_called_once()


@pytest.mark.asyncio
async def test_daft_query_returns_empty_warnings(client: AsyncClient, mock_lake: MagicMock) -> None:
    resp = await client.post(
        "/api/v1/datasets/docs/query/daft",
        json={},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["warnings"] == []


@pytest.mark.asyncio
async def test_daft_query_returns_warnings_from_feasibility(client: AsyncClient, mock_lake: MagicMock) -> None:
    frame = mock_lake.daft_query.return_value
    frame.check_feasibility.return_value = ["consider DuckDB OLAP"]
    resp = await client.post(
        "/api/v1/datasets/docs/query/daft",
        json={},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert "consider DuckDB OLAP" in body["warnings"]


@pytest.mark.asyncio
async def test_daft_query_hard_limit_returns_422(client: AsyncClient, mock_lake: MagicMock) -> None:
    frame = mock_lake.daft_query.return_value
    frame.check_feasibility.side_effect = RuntimeError(
        "Dataset has 2,000,000 rows. Use DuckDB OLAP"
    )
    resp = await client.post(
        "/api/v1/datasets/docs/query/daft",
        json={},
    )
    assert resp.status_code == 422
    body = resp.json()
    assert "DuckDB OLAP" in body["detail"]


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
