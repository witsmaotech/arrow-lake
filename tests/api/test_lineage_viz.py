"""Tests for lineage router visualization helpers and edge cases.

Supplements test_lineage.py and test_lineage_extra.py with:
- _sanitize_node_id (dots, dashes, spaces)
- _to_mermaid / _to_dot visualization
- lineage_graph format=mermaid and format=dot
- lineage_query with different result types (to_pylist, list, single object)
- lineage_stats with getattr fallback for object-like events
"""

from __future__ import annotations

from dataclasses import dataclass
from unittest.mock import MagicMock

import pytest
from arrow_lake.api.app import create_app
from arrow_lake.api.routers.lineage import _sanitize_node_id, _to_mermaid, _to_dot
from arrow_lake.config import ArrowLakeConfig
from httpx import ASGITransport, AsyncClient
from starlette.responses import PlainTextResponse


# ---------------------------------------------------------------------------
# _sanitize_node_id — unit tests
# ---------------------------------------------------------------------------


class TestSanitizeNodeId:
    """Node ID sanitization for Mermaid/DOT."""

    def test_dots(self) -> None:
        assert _sanitize_node_id("my.dataset") == "my_dataset"

    def test_dashes(self) -> None:
        assert _sanitize_node_id("my-dataset") == "my_dataset"

    def test_spaces(self) -> None:
        assert _sanitize_node_id("my dataset") == "my_dataset"

    def test_combined(self) -> None:
        assert _sanitize_node_id("my.data-set name") == "my_data_set_name"

    def test_clean_name(self) -> None:
        assert _sanitize_node_id("clean_name") == "clean_name"


# ---------------------------------------------------------------------------
# _to_mermaid — unit tests
# ---------------------------------------------------------------------------


class TestToMermaid:
    """Mermaid flowchart output."""

    def test_basic_graph(self) -> None:
        graph = {
            "nodes": [
                {"id": "raw_data"},
                {"id": "processed"},
            ],
            "edges": [
                {"from": "raw_data", "to": "processed", "operation": "transform"},
            ],
        }
        result = _to_mermaid("test", graph)
        assert isinstance(result, PlainTextResponse)
        assert result.media_type == "text/x-mermaid"
        text = result.body.decode()
        assert "graph LR" in text
        assert "raw_data -->|transform| processed" in text

    def test_isolated_nodes(self) -> None:
        graph = {
            "nodes": [
                {"id": "orphan"},
                {"id": "connected"},
            ],
            "edges": [
                {"from": "src", "to": "connected", "operation": "load"},
            ],
        }
        result = _to_mermaid("test", graph)
        text = result.body.decode()
        # orphan should appear as isolated node
        assert 'orphan["orphan"]' in text  # mermaid quotes node labels now

    def test_empty_graph(self) -> None:
        graph = {"nodes": [], "edges": []}
        result = _to_mermaid("test", graph)
        text = result.body.decode()
        assert "graph LR" in text

    def test_special_chars_in_node_ids(self) -> None:
        graph = {
            "nodes": [],
            "edges": [
                {"from": "my.dataset", "to": "out-put", "operation": "etl"},
            ],
        }
        result = _to_mermaid("test", graph)
        text = result.body.decode()
        assert "my_dataset -->|etl| out_put" in text


# ---------------------------------------------------------------------------
# _to_dot — unit tests
# ---------------------------------------------------------------------------


class TestToDot:
    """Graphviz DOT output."""

    def test_basic_graph(self) -> None:
        graph = {
            "nodes": [
                {"id": "raw_data", "type": "source"},
                {"id": "processed", "type": "derived"},
            ],
            "edges": [
                {"from": "raw_data", "to": "processed", "operation": "transform"},
            ],
        }
        result = _to_dot("test", graph)
        assert isinstance(result, PlainTextResponse)
        assert result.media_type == "text/vnd.graphviz"
        text = result.body.decode()
        assert "digraph lineage" in text
        assert "rankdir=LR" in text
        assert 'label="Lineage: test"' in text

    def test_node_colors_by_type(self) -> None:
        graph = {
            "nodes": [
                {"id": "src", "type": "source"},
                {"id": "tgt", "type": "target"},
                {"id": "der", "type": "derived"},
                {"id": "unk", "type": "unknown"},
            ],
            "edges": [],
        }
        result = _to_dot("test", graph)
        text = result.body.decode()
        assert "#2196F3" in text  # source color
        assert "#4CAF50" in text  # target color
        assert "#FF9800" in text  # derived color
        assert "#9E9E9E" in text  # default color

    def test_empty_graph(self) -> None:
        graph = {"nodes": [], "edges": []}
        result = _to_dot("test", graph)
        text = result.body.decode()
        assert "digraph lineage" in text
        assert text.strip().endswith("}")


# ---------------------------------------------------------------------------
# HTTP integration — format=mermaid and format=dot
# ---------------------------------------------------------------------------


@pytest.fixture
def mock_lake() -> MagicMock:
    lake = MagicMock()
    lake.lineage_graph.return_value = {
        "nodes": [
            {"id": "raw", "depth": 0, "type": "source"},
            {"id": "clean", "depth": 1, "type": "derived"},
        ],
        "edges": [
            {"from": "raw", "to": "clean", "operation": "etl"},
        ],
        "stats": {"total_nodes": 2, "total_edges": 1, "max_depth": 1},
    }
    lake.lineage_record_event.return_value = None
    lake.lineage_history.return_value = []
    lake.lineage_query.return_value = []
    lake.lineage_impact.return_value = []
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


class TestLineageGraphMermaid:
    """GET /lineage/graph/{ds}?format=mermaid"""

    @pytest.mark.asyncio
    async def test_returns_mermaid_format(
        self, client: AsyncClient, mock_lake: MagicMock,
    ) -> None:
        resp = await client.get("/api/v1/lineage/graph/docs?format=mermaid")
        assert resp.status_code == 200
        assert "text/x-mermaid" in resp.headers.get("content-type", "")
        assert "graph LR" in resp.text
        assert "raw -->|etl| clean" in resp.text


class TestLineageGraphDot:
    """GET /lineage/graph/{ds}?format=dot"""

    @pytest.mark.asyncio
    async def test_returns_dot_format(
        self, client: AsyncClient, mock_lake: MagicMock,
    ) -> None:
        resp = await client.get("/api/v1/lineage/graph/docs?format=dot")
        assert resp.status_code == 200
        assert "text/vnd.graphviz" in resp.headers.get("content-type", "")
        assert "digraph lineage" in resp.text
        assert 'label="Lineage: docs"' in resp.text


# ---------------------------------------------------------------------------
# lineage_query — different result types
# ---------------------------------------------------------------------------


class TestLineageQueryResultTypes:
    """POST /lineage/query handles Arrow table, list, and single object."""

    @pytest.mark.asyncio
    async def test_query_with_list_result(
        self, client: AsyncClient, mock_lake: MagicMock,
    ) -> None:
        mock_lake.lineage_query.return_value = [
            {"dataset": "docs", "op": "create"},
            {"dataset": "images", "op": "create"},
        ]
        resp = await client.post(
            "/api/v1/lineage/query",
            json={"sql": "SELECT * FROM events"},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert len(body["data"]) == 2

    @pytest.mark.asyncio
    async def test_query_with_pylist_result(
        self, client: AsyncClient, mock_lake: MagicMock,
    ) -> None:
        import pyarrow as pa

        tbl = pa.table({"dataset": ["docs"], "op": ["create"]})
        mock_lake.lineage_query.return_value = tbl
        resp = await client.post(
            "/api/v1/lineage/query",
            json={"sql": "SELECT * FROM events"},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert len(body["data"]) == 1
        assert body["data"][0]["dataset"] == "docs"

    @pytest.mark.asyncio
    async def test_query_with_single_dict_result(
        self, client: AsyncClient, mock_lake: MagicMock,
    ) -> None:
        mock_lake.lineage_query.return_value = {"dataset": "docs", "op": "create"}
        resp = await client.post(
            "/api/v1/lineage/query",
            json={"sql": "SELECT * FROM events LIMIT 1"},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert len(body["data"]) == 1


# ---------------------------------------------------------------------------
# lineage_stats — getattr fallback for object-like events
# ---------------------------------------------------------------------------


class TestLineageStatsWithObjects:
    """GET /lineage/stats handles both dict and object-like events."""

    @pytest.mark.asyncio
    async def test_stats_with_mixed_event_types(
        self, client: AsyncClient, mock_lake: MagicMock,
    ) -> None:
        @dataclass
        class ObjEvent:
            dataset_name: str = "obj_ds"
            operation: str = "create"

        mock_lake.lineage_history.return_value = [
            {"dataset_name": "dict_ds", "operation": "append"},
            ObjEvent(),
        ]
        resp = await client.get("/api/v1/lineage/stats")
        assert resp.status_code == 200
        body = resp.json()
        assert body["total_datasets_tracked"] == 2
        assert body["total_events"] == 2

    @pytest.mark.asyncio
    async def test_stats_with_object_no_dataset_name(
        self, client: AsyncClient, mock_lake: MagicMock,
    ) -> None:
        mock_lake.lineage_history.return_value = [
            MagicMock(spec=[]),  # no dataset_name attribute
        ]
        resp = await client.get("/api/v1/lineage/stats")
        assert resp.status_code == 200
        body = resp.json()
        assert body["total_events"] == 1
        assert body["total_datasets_tracked"] == 0


# ---------------------------------------------------------------------------
# lineage_history — serialization of non-dict events
# ---------------------------------------------------------------------------


class TestLineageHistorySerialization:
    """GET /lineage/history/{ds} serializes non-dict events as str()."""

    @pytest.mark.asyncio
    async def test_history_with_string_event(
        self, client: AsyncClient, mock_lake: MagicMock,
    ) -> None:
        mock_lake.lineage_history.return_value = [
            "simple_event_string",
            {"operation": "create"},
        ]
        resp = await client.get("/api/v1/lineage/history/docs")
        assert resp.status_code == 200
        body = resp.json()
        assert len(body["events"]) == 2
        # Non-dict event should be wrapped as {"event": str(event)}
        assert body["events"][0]["event"] == "simple_event_string"
        assert body["events"][1]["operation"] == "create"
