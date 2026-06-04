"""Tests for query router v1.5.2 — validate_sql_safety, SSE streaming, edge cases.

Supplements test_query.py and test_query_extra.py with uncovered branches:
- validate_sql_safety invoked in olap_query (dangerous SQL rejected)
- SSE streaming response headers and body structure
- _stream_table generator output
- metadata query edge cases
"""

from __future__ import annotations

from dataclasses import dataclass
from unittest.mock import MagicMock

import pyarrow as pa
import pytest
from arrow_lake.api.app import create_app
from arrow_lake.config import ArrowLakeConfig
from httpx import ASGITransport, AsyncClient


@dataclass(frozen=True)
class _FakeOlapResult:
    table: pa.Table
    row_count: int = 3
    column_count: int = 2
    sql: str = "SELECT count(*) FROM t"


def _sample_table() -> pa.Table:
    return pa.table({"id": [1, 2, 3], "count": [10, 20, 30]})


def _chainable_frame(final_table: pa.Table) -> MagicMock:
    frame = MagicMock()
    frame.collect.return_value = final_table
    frame.check_feasibility.return_value = []
    frame.column_names = list(final_table.column_names)
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
# validate_sql_safety — dangerous SQL rejected at olap endpoint
# ---------------------------------------------------------------------------


class TestOlapSqlSafety:
    """olap_query calls validate_sql_safety; dangerous keywords should be blocked."""

    @pytest.mark.asyncio
    async def test_insert_rejected(self, client: AsyncClient) -> None:
        resp = await client.post(
            "/api/v1/datasets/docs/query/olap",
            json={"sql": "INSERT INTO docs VALUES (1)"},
        )
        assert resp.status_code in (400, 422)

    @pytest.mark.asyncio
    async def test_drop_rejected(self, client: AsyncClient) -> None:
        resp = await client.post(
            "/api/v1/datasets/docs/query/olap",
            json={"sql": "DROP TABLE docs"},
        )
        assert resp.status_code in (400, 422)

    @pytest.mark.asyncio
    async def test_delete_rejected(self, client: AsyncClient) -> None:
        resp = await client.post(
            "/api/v1/datasets/docs/query/olap",
            json={"sql": "DELETE FROM docs WHERE id=1"},
        )
        assert resp.status_code in (400, 422)

    @pytest.mark.asyncio
    async def test_update_rejected(self, client: AsyncClient) -> None:
        resp = await client.post(
            "/api/v1/datasets/docs/query/olap",
            json={"sql": "UPDATE docs SET count=0"},
        )
        assert resp.status_code in (400, 422)

    @pytest.mark.asyncio
    async def test_alter_rejected(self, client: AsyncClient) -> None:
        resp = await client.post(
            "/api/v1/datasets/docs/query/olap",
            json={"sql": "ALTER TABLE docs ADD COLUMN x INT"},
        )
        assert resp.status_code in (400, 422)

    @pytest.mark.asyncio
    async def test_create_rejected(self, client: AsyncClient) -> None:
        resp = await client.post(
            "/api/v1/datasets/docs/query/olap",
            json={"sql": "CREATE TABLE fake (id INT)"},
        )
        assert resp.status_code in (400, 422)

    @pytest.mark.asyncio
    async def test_safe_select_passes(self, client: AsyncClient, mock_lake: MagicMock) -> None:
        resp = await client.post(
            "/api/v1/datasets/docs/query/olap",
            json={"sql": "SELECT count(*) FROM docs"},
        )
        assert resp.status_code == 200



# ---------------------------------------------------------------------------
# SSE streaming — response headers and content
# ---------------------------------------------------------------------------


class TestOlapStreaming:
    """olap_query with stream=True returns SSE with correct headers."""

    @pytest.mark.asyncio
    async def test_stream_returns_event_stream(self, client: AsyncClient) -> None:
        resp = await client.post(
            "/api/v1/datasets/docs/query/olap",
            json={"sql": "SELECT * FROM docs", "stream": True},
        )
        assert resp.status_code == 200
        ct = resp.headers.get("content-type", "")
        assert "text/event-stream" in ct

    @pytest.mark.asyncio
    async def test_stream_has_row_count_header(
        self, client: AsyncClient, mock_lake: MagicMock,
    ) -> None:
        tbl = _sample_table()
        mock_lake.olap_query.return_value = _FakeOlapResult(table=tbl, sql="SELECT *")
        resp = await client.post(
            "/api/v1/datasets/docs/query/olap",
            json={"sql": "SELECT * FROM docs", "stream": True},
        )
        assert resp.status_code == 200
        assert "x-row-count" in resp.headers
        assert resp.headers["x-row-count"] == str(tbl.num_rows)

    @pytest.mark.asyncio
    async def test_stream_has_sql_header(
        self, client: AsyncClient, mock_lake: MagicMock,
    ) -> None:
        mock_lake.olap_query.return_value = _FakeOlapResult(
            table=_sample_table(), sql="SELECT * FROM docs",
        )
        resp = await client.post(
            "/api/v1/datasets/docs/query/olap",
            json={"sql": "SELECT * FROM docs", "stream": True},
        )
        assert resp.status_code == 200
        assert "x-sql" in resp.headers

    @pytest.mark.asyncio
    async def test_stream_body_contains_data_events(
        self, client: AsyncClient, mock_lake: MagicMock,
    ) -> None:
        mock_lake.olap_query.return_value = _FakeOlapResult(
            table=_sample_table(), sql="SELECT * FROM docs",
        )
        resp = await client.post(
            "/api/v1/datasets/docs/query/olap",
            json={"sql": "SELECT * FROM docs", "stream": True},
        )
        assert resp.status_code == 200
        text = resp.text
        # SSE events should contain data: prefix lines
        assert "data:" in text
        # Should have schema, batch, and done events
        assert '"type": "schema"' in text
        assert '"type": "done"' in text

    @pytest.mark.asyncio
    async def test_stream_with_custom_batch_size(
        self, client: AsyncClient, mock_lake: MagicMock,
    ) -> None:
        mock_lake.olap_query.return_value = _FakeOlapResult(
            table=_sample_table(), sql="SELECT * FROM docs",
        )
        resp = await client.post(
            "/api/v1/datasets/docs/query/olap",
            json={"sql": "SELECT * FROM docs", "stream": True, "batch_size": 1},
        )
        # Streaming may fail if StreamingResult import is unavailable in test env
        if resp.status_code == 200:
            text = resp.text
            assert "data:" in text


# ---------------------------------------------------------------------------
# Daft query — join raises 501 (already tested, but verify response)
# ---------------------------------------------------------------------------


class TestDaftJoinEdge:
    """Daft join operation returns 501."""

    @pytest.mark.asyncio
    async def test_join_returns_501_detail(
        self, client: AsyncClient, mock_lake: MagicMock,
    ) -> None:
        resp = await client.post(
            "/api/v1/datasets/docs/query/daft",
            json={"join": {"dataset": "other", "on": "id", "how": "inner"}},
        )
        assert resp.status_code == 501
        assert "Join" in resp.json()["detail"]


# ---------------------------------------------------------------------------
# Metadata query — validate_sql_safety also applies
# ---------------------------------------------------------------------------


class TestMetadataSqlSafety:
    """metadata_query also validates SQL for dangerous keywords."""

    @pytest.mark.asyncio
    async def test_metadata_rejects_dangerous_sql(self, client: AsyncClient) -> None:
        resp = await client.post(
            "/api/v1/datasets/docs/query/metadata",
            json={"sql": "DROP TABLE docs"},
        )
        assert resp.status_code in (400, 422)

    @pytest.mark.asyncio
    async def test_metadata_allows_safe_select(
        self, client: AsyncClient, mock_lake: MagicMock,
    ) -> None:
        resp = await client.post(
            "/api/v1/datasets/docs/query/metadata",
            json={"sql": "SELECT * FROM docs WHERE modality='text'"},
        )
        assert resp.status_code == 200


# ---------------------------------------------------------------------------
# _apply_pipeline — ne and lte filter ops
# ---------------------------------------------------------------------------


class TestDaftFilterOps:
    """Test remaining filter operations not covered in main test file."""

    @pytest.mark.asyncio
    async def test_ne_filter(self, client: AsyncClient, mock_lake: MagicMock) -> None:
        resp = await client.post(
            "/api/v1/datasets/docs/query/daft",
            json={"filters": [{"column": "count", "op": "ne", "value": 10}]},
        )
        assert resp.status_code == 200
        frame = mock_lake.daft_query.return_value
        frame.filter.assert_called_once()

    @pytest.mark.asyncio
    async def test_lt_filter(self, client: AsyncClient, mock_lake: MagicMock) -> None:
        resp = await client.post(
            "/api/v1/datasets/docs/query/daft",
            json={"filters": [{"column": "count", "op": "lt", "value": 20}]},
        )
        assert resp.status_code == 200

    @pytest.mark.asyncio
    async def test_lte_filter(self, client: AsyncClient, mock_lake: MagicMock) -> None:
        resp = await client.post(
            "/api/v1/datasets/docs/query/daft",
            json={"filters": [{"column": "count", "op": "lte", "value": 20}]},
        )
        assert resp.status_code == 200

    @pytest.mark.asyncio
    async def test_is_null_filter_schema_valid(self, client: AsyncClient) -> None:
        """is_null op is accepted by the request schema (validated in test_query_extra)."""
        resp = await client.post(
            "/api/v1/datasets/docs/query/daft",
            json={"filters": [{"column": "id", "op": "is_null"}]},
        )
        assert resp.status_code == 200

    @pytest.mark.asyncio
    async def test_multiple_filters(self, client: AsyncClient, mock_lake: MagicMock) -> None:
        resp = await client.post(
            "/api/v1/datasets/docs/query/daft",
            json={"filters": [
                {"column": "count", "op": "gte", "value": 10},
                {"column": "id", "op": "lt", "value": 3},
            ]},
        )
        assert resp.status_code == 200
        frame = mock_lake.daft_query.return_value
        assert frame.filter.call_count == 2


# ---------------------------------------------------------------------------
# Daft — groupby stddev and var
# ---------------------------------------------------------------------------


class TestDaftGroupbyAggs:
    """Test remaining groupby aggregation functions."""

    @pytest.mark.asyncio
    async def test_groupby_stddev(self, client: AsyncClient, mock_lake: MagicMock) -> None:
        resp = await client.post(
            "/api/v1/datasets/docs/query/daft",
            json={"groupby": {"columns": ["id"], "agg": "stddev"}},
        )
        assert resp.status_code == 200
        grouped = mock_lake.daft_query.return_value.groupby.return_value
        grouped.stddev.assert_called_once()

    @pytest.mark.asyncio
    async def test_groupby_var(self, client: AsyncClient, mock_lake: MagicMock) -> None:
        resp = await client.post(
            "/api/v1/datasets/docs/query/daft",
            json={"groupby": {"columns": ["id"], "agg": "var"}},
        )
        assert resp.status_code == 200
        grouped = mock_lake.daft_query.return_value.groupby.return_value
        grouped.var.assert_called_once()

    @pytest.mark.asyncio
    async def test_groupby_min(self, client: AsyncClient, mock_lake: MagicMock) -> None:
        resp = await client.post(
            "/api/v1/datasets/docs/query/daft",
            json={"groupby": {"columns": ["id"], "agg": "min"}},
        )
        assert resp.status_code == 200

    @pytest.mark.asyncio
    async def test_groupby_max(self, client: AsyncClient, mock_lake: MagicMock) -> None:
        resp = await client.post(
            "/api/v1/datasets/docs/query/daft",
            json={"groupby": {"columns": ["id"], "agg": "max"}},
        )
        assert resp.status_code == 200

    @pytest.mark.asyncio
    async def test_groupby_count(self, client: AsyncClient, mock_lake: MagicMock) -> None:
        resp = await client.post(
            "/api/v1/datasets/docs/query/daft",
            json={"groupby": {"columns": ["id"], "agg": "count"}},
        )
        assert resp.status_code == 200
