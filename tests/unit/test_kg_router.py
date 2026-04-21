"""Tests for knowledge graph API router."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any
from unittest.mock import AsyncMock, MagicMock

from arrow_lake.exceptions import ErrorCode, KGError
from arrow_lake.rag.pipeline import RAGCitation, RAGResponse
from fastapi.testclient import TestClient

# ---------------------------------------------------------------------------
# Mock Lake & Config
# ---------------------------------------------------------------------------


@dataclass
class MockHugeGraphConfig:
    enabled: bool = True


@dataclass
class MockConfig:
    hugegraph: MockHugeGraphConfig = field(default_factory=MockHugeGraphConfig)


@dataclass
class MockLake:
    _config: MockConfig = field(default_factory=MockConfig)


def _make_app(lake: Any) -> Any:
    """Create a test FastAPI app with the kg router and a fixed lake."""
    from arrow_lake.api.routers.knowledge_graph import router
    from fastapi import FastAPI

    app = FastAPI()
    app.state.lake = lake
    app.include_router(router)
    return app


# ---------------------------------------------------------------------------
# POST /api/v2/kg/build
# ---------------------------------------------------------------------------


class TestKGBuildEndpoint:
    def test_build_success(self) -> None:
        lake = MockLake()
        lake.kg_build = AsyncMock(return_value="task-123")
        app = _make_app(lake)
        client = TestClient(app)
        resp = client.post("/api/v2/kg/build", json={"dataset_name": "my_ds"})
        assert resp.status_code == 200
        data = resp.json()
        assert data["task_id"] == "task-123"
        assert data["status"] == "pending"

    def test_build_kg_not_enabled(self) -> None:
        lake = MockLake()
        lake._config = MockConfig(hugegraph=MockHugeGraphConfig(enabled=False))
        lake.kg_build = AsyncMock(
            side_effect=KGError(
                ErrorCode.KG_GRAPH_NOT_FOUND,
                "KG not enabled",
            )
        )
        app = _make_app(lake)
        client = TestClient(app)
        resp = client.post("/api/v2/kg/build", json={"dataset_name": "my_ds"})
        assert resp.status_code == 404

    def test_build_connection_failed(self) -> None:
        lake = MockLake()
        lake.kg_build = AsyncMock(
            side_effect=KGError(
                ErrorCode.KG_CONNECTION_FAILED,
                "Cannot connect to HugeGraph",
            )
        )
        app = _make_app(lake)
        client = TestClient(app)
        resp = client.post("/api/v2/kg/build", json={"dataset_name": "my_ds"})
        assert resp.status_code == 503


# ---------------------------------------------------------------------------
# GET /api/v2/kg/build/{task_id}/status
# ---------------------------------------------------------------------------


class TestKGBuildStatusEndpoint:
    def test_status_found(self) -> None:
        lake = MockLake()
        lake.kg_build_status = AsyncMock(return_value={
            "task_id": "t1",
            "status": "completed",
            "dataset_name": "ds",
            "total_chunks": 10,
            "processed_chunks": 10,
            "entity_count": 5,
            "relation_count": 3,
            "error": None,
        })
        app = _make_app(lake)
        client = TestClient(app)
        resp = client.get("/api/v2/kg/build/t1/status")
        assert resp.status_code == 200
        data = resp.json()
        assert data["task_id"] == "t1"
        assert data["entity_count"] == 5

    def test_status_not_found(self) -> None:
        lake = MockLake()
        lake.kg_build_status = AsyncMock(return_value=None)
        app = _make_app(lake)
        client = TestClient(app)
        resp = client.get("/api/v2/kg/build/nonexistent/status")
        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# GET /api/v2/kg/schema
# ---------------------------------------------------------------------------


class TestKGSchemaEndpoint:
    def test_schema_success(self) -> None:
        lake = MockLake()
        mock_client = MagicMock()
        mock_client.get_schema = AsyncMock(return_value={
            "vertexlabels": [{"name": "Person"}, {"name": "Document"}],
            "edgelabels": [{"name": "mentions"}],
        })
        lake._get_kg_client = MagicMock(return_value=mock_client)
        app = _make_app(lake)
        client = TestClient(app)
        resp = client.get("/api/v2/kg/schema")
        assert resp.status_code == 200
        data = resp.json()
        assert data["vertex_labels"] == ["Person", "Document"]
        assert data["edge_labels"] == ["mentions"]

    def test_schema_kg_not_enabled(self) -> None:
        lake = MockLake()
        lake._get_kg_client = MagicMock(return_value=None)
        app = _make_app(lake)
        client = TestClient(app)
        resp = client.get("/api/v2/kg/schema")
        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# POST /api/v2/kg/query
# ---------------------------------------------------------------------------


class TestKGQueryEndpoint:
    def test_query_success(self) -> None:
        lake = MockLake()
        lake.kg_query = AsyncMock(return_value=[100])
        app = _make_app(lake)
        client = TestClient(app)
        resp = client.post("/api/v2/kg/query", json={"gremlin": "g.V().count()"})
        assert resp.status_code == 200
        data = resp.json()
        assert data["results"] == [100]
        assert "execution_time_ms" in data

    def test_query_kg_not_enabled(self) -> None:
        lake = MockLake()
        lake.kg_query = AsyncMock(
            side_effect=KGError(ErrorCode.KG_GRAPH_NOT_FOUND, "not enabled")
        )
        app = _make_app(lake)
        client = TestClient(app)
        resp = client.post("/api/v2/kg/query", json={"gremlin": "g.V()"})
        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# GET /api/v2/kg/entities/{entity_id}/neighbors
# ---------------------------------------------------------------------------


class TestKGNeighborsEndpoint:
    def test_neighbors_success(self) -> None:
        lake = MockLake()
        lake.kg_get_neighbors = AsyncMock(return_value=[{"id": "v2"}])
        app = _make_app(lake)
        client = TestClient(app)
        resp = client.get("/api/v2/kg/entities/v1/neighbors?depth=2")
        assert resp.status_code == 200
        data = resp.json()
        assert data["center_id"] == "v1"
        assert data["depth"] == 2


# ---------------------------------------------------------------------------
# GET /api/v2/kg/stats
# ---------------------------------------------------------------------------


class TestKGStatsEndpoint:
    def test_stats_success(self) -> None:
        lake = MockLake()
        lake.kg_stats = AsyncMock(return_value={"total_vertices": 42, "total_edges": 99})
        app = _make_app(lake)
        client = TestClient(app)
        resp = client.get("/api/v2/kg/stats")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total_vertices"] == 42
        assert data["graph_enabled"] is True


# ---------------------------------------------------------------------------
# DELETE /api/v2/kg/graph
# ---------------------------------------------------------------------------


class TestKGDeleteGraphEndpoint:
    def test_delete_success(self) -> None:
        lake = MockLake()
        lake.kg_delete_graph = AsyncMock(return_value=None)
        app = _make_app(lake)
        client = TestClient(app)
        resp = client.delete("/api/v2/kg/graph")
        assert resp.status_code == 200
        assert resp.json()["status"] == "ok"


# ---------------------------------------------------------------------------
# POST /api/v2/kg/query/graphrag
# ---------------------------------------------------------------------------


class TestGraphRAGEndpoint:
    def test_graphrag_success(self) -> None:
        lake = MockLake()
        citation = RAGCitation(
            chunk_index=0,
            dataset="ds",
            row_id="r1",
            score=0.9,
            text_excerpt="hello",
        )
        rag_resp = RAGResponse(
            answer="The answer is 42.",
            citations=(citation,),
            retrieval_count=1,
            context_tokens=100,
            latency_ms=50.0,
            session_id="s1",
        )
        lake.rag_query = AsyncMock(return_value=rag_resp)
        app = _make_app(lake)
        client = TestClient(app)
        resp = client.post(
            "/api/v2/kg/query/graphrag",
            json={"question": "What is the answer?", "dataset_name": "ds"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["answer"] == "The answer is 42."
        assert len(data["citations"]) == 1
