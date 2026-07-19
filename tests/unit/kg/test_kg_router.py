"""Tests for knowledge graph API router."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any
from unittest.mock import AsyncMock, MagicMock

from arrow_lake.api.auth_models import Role, TokenPayload
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
class MockAuthConfig:
    allow_unauthenticated_access: bool = True


@dataclass
class MockConfig:
    hugegraph: MockHugeGraphConfig = field(default_factory=MockHugeGraphConfig)
    auth: MockAuthConfig = field(default_factory=MockAuthConfig)


@dataclass
class MockLake:
    _config: MockConfig = field(default_factory=MockConfig)


def _make_app(lake: Any) -> Any:
    """Create a test FastAPI app with the kg router and a fixed lake."""
    from arrow_lake.api.routers.knowledge_graph import router
    from fastapi import FastAPI, Request

    app = FastAPI()
    app.state.lake = lake
    app.state.config = lake._config

    @app.middleware("http")
    async def _inject_user(request: Request, call_next):
        request.state.user = TokenPayload(sub="test-admin", role=Role.ADMIN, exp=0, iat=0)
        return await call_next(request)

    app.include_router(router)
    return app


# ---------------------------------------------------------------------------
# POST /api/v1/kg/build
# ---------------------------------------------------------------------------


class TestKGBuildEndpoint:
    def test_build_success(self) -> None:
        lake = MockLake()
        lake.kg_build = AsyncMock(return_value="task-123")
        app = _make_app(lake)
        client = TestClient(app)
        resp = client.post("/api/v1/kg/build", json={"dataset": "my_ds"})
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
        resp = client.post("/api/v1/kg/build", json={"dataset": "my_ds"})
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
        resp = client.post("/api/v1/kg/build", json={"dataset": "my_ds"})
        assert resp.status_code == 502


# ---------------------------------------------------------------------------
# GET /api/v1/kg/build/{task_id}/status
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
        resp = client.get("/api/v1/kg/build/t1/status")
        assert resp.status_code == 200
        data = resp.json()
        assert data["task_id"] == "t1"
        assert data["entity_count"] == 5

    def test_status_not_found(self) -> None:
        lake = MockLake()
        lake.kg_build_status = AsyncMock(return_value=None)
        app = _make_app(lake)
        client = TestClient(app)
        resp = client.get("/api/v1/kg/build/nonexistent/status")
        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# GET /api/v1/kg/schema
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
        resp = client.get("/api/v1/kg/schema")
        assert resp.status_code == 200
        data = resp.json()
        assert data["vertex_labels"] == ["Person", "Document"]
        assert data["edge_labels"] == ["mentions"]

    def test_schema_kg_not_enabled(self) -> None:
        lake = MockLake()
        lake._get_kg_client = MagicMock(return_value=None)
        app = _make_app(lake)
        client = TestClient(app)
        resp = client.get("/api/v1/kg/schema")
        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# POST /api/v1/kg/query
# ---------------------------------------------------------------------------


class TestKGQueryEndpoint:
    def test_query_success(self) -> None:
        lake = MockLake()
        lake.kg_query = AsyncMock(return_value=[100])
        app = _make_app(lake)
        client = TestClient(app)
        resp = client.post("/api/v1/kg/query", json={"gremlin": "g.V().count()"})
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
        resp = client.post("/api/v1/kg/query", json={"gremlin": "g.V()"})
        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# GET /api/v1/kg/entities/{entity_id}/neighbors
# ---------------------------------------------------------------------------


class TestKGNeighborsEndpoint:
    def test_neighbors_success(self) -> None:
        lake = MockLake()
        lake.kg_get_neighbors = AsyncMock(return_value=[{"id": "v2"}])
        app = _make_app(lake)
        client = TestClient(app)
        resp = client.get("/api/v1/kg/entities/v1/neighbors?depth=2")
        assert resp.status_code == 200
        data = resp.json()
        assert data["center_id"] == "v1"
        assert data["depth"] == 2


# ---------------------------------------------------------------------------
# GET /api/v1/kg/stats
# ---------------------------------------------------------------------------


class TestKGStatsEndpoint:
    def test_stats_success(self) -> None:
        lake = MockLake()
        lake.kg_stats = AsyncMock(return_value={"total_vertices": 42, "total_edges": 99})
        app = _make_app(lake)
        client = TestClient(app)
        resp = client.get("/api/v1/kg/stats")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total_vertices"] == 42
        assert data["graph_enabled"] is True


# ---------------------------------------------------------------------------
# DELETE /api/v1/kg/graph
# ---------------------------------------------------------------------------


class TestKGDeleteGraphEndpoint:
    def test_delete_success(self) -> None:
        lake = MockLake()
        lake.kg_delete_graph = AsyncMock(return_value=None)
        app = _make_app(lake)
        client = TestClient(app)
        resp = client.delete("/api/v1/kg/graph")
        assert resp.status_code == 200
        assert resp.json()["status"] == "ok"


# ---------------------------------------------------------------------------
# POST /api/v1/kg/query/graphrag
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
            "/api/v1/kg/query/graphrag",
            json={"question": "What is the answer?", "dataset": "ds"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["answer"] == "The answer is 42."
        assert len(data["citations"]) == 1


# ---------------------------------------------------------------------------
# GET /api/v1/kg/doc-types | /templates | /templates/{path}  (v1.8.8)
# ---------------------------------------------------------------------------


class TestKGDocTypeMetadataEndpoints:
    def _doc_type(self) -> dict[str, Any]:
        return {
            "doc_type": "paper",
            "description": "academic / technical paper",
            "aliases": ["article", "论文"],
            "resolved_template": "general/concept_graph",
            "resolution": "gallery",
        }

    def _summary(self) -> dict[str, Any]:
        return {
            "path": "general/concept_graph",
            "category": "general",
            "name": "concept_graph",
            "type": "graph",
            "tags": ["general", "concept"],
            "is_high_risk": False,
            "description_zh": "概念关系图",
            "description_en": "Concept Graph",
        }

    def _detail(self) -> dict[str, Any]:
        return {
            **self._summary(),
            "entity_fields": ["name", "type"],
            "relation_fields": ["source", "target"],
            "guideline_zh": "你是知识图谱专家",
            "guideline_en": "You are a KG expert",
        }

    def test_doc_types_ok(self) -> None:
        lake = MockLake()
        lake.kg_list_doc_types = AsyncMock(return_value=[self._doc_type()])
        client = TestClient(_make_app(lake))
        resp = client.get("/api/v1/kg/doc-types")
        assert resp.status_code == 200
        data = resp.json()
        assert data["doc_types"][0]["doc_type"] == "paper"
        assert data["doc_types"][0]["resolution"] == "gallery"

    def test_templates_ok_with_count(self) -> None:
        lake = MockLake()
        lake.kg_list_templates = AsyncMock(return_value=[self._summary()])
        client = TestClient(_make_app(lake))
        resp = client.get("/api/v1/kg/templates")
        assert resp.status_code == 200
        data = resp.json()
        assert data["count"] == 1
        assert data["templates"][0]["path"] == "general/concept_graph"

    def test_template_detail_ok(self) -> None:
        lake = MockLake()
        lake.kg_describe_template = AsyncMock(return_value=self._detail())
        client = TestClient(_make_app(lake))
        # path with a slash — :path converter must accept it
        resp = client.get("/api/v1/kg/templates/general/concept_graph")
        assert resp.status_code == 200
        data = resp.json()
        assert data["template"]["path"] == "general/concept_graph"
        assert data["template"]["entity_fields"] == ["name", "type"]

    def test_template_detail_not_found(self) -> None:
        lake = MockLake()
        lake.kg_describe_template = AsyncMock(
            side_effect=KGError(ErrorCode.KG_GRAPH_NOT_FOUND, "not found")
        )
        client = TestClient(_make_app(lake))
        resp = client.get("/api/v1/kg/templates/no/such")
        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# POST /api/v1/kg/search  +  POST /api/v1/kg/ask  (v1.8.8 [#2] KA RAG)
# ---------------------------------------------------------------------------


class TestKGSearchEndpoint:
    def test_search_success(self) -> None:
        lake = MockLake()
        lake.kg_search = AsyncMock(return_value={
            "nodes": [{"type": "concept", "name": "聚合根"}],
            "edges": [],
            "node_count": 1,
            "edge_count": 0,
        })
        client = TestClient(_make_app(lake))
        resp = client.post("/api/v1/kg/search", json={"dataset": "jd_ddd", "query": "聚合根", "top_k": 5})
        assert resp.status_code == 200
        data = resp.json()
        assert data["node_count"] == 1
        assert data["nodes"][0]["name"] == "聚合根"
        lake.kg_search.assert_awaited_once_with("jd_ddd", "聚合根", top_k=5)

    def test_search_kg_not_enabled(self) -> None:
        lake = MockLake()
        lake.kg_search = AsyncMock(
            side_effect=KGError(ErrorCode.KG_QUERY_FAILED, "requires extractor_backend=he")
        )
        client = TestClient(_make_app(lake))
        resp = client.post("/api/v1/kg/search", json={"dataset": "ds", "query": "q"})
        assert resp.status_code == 500  # KG_QUERY_FAILED → 500


class TestKGAskEndpoint:
    def test_ask_success(self) -> None:
        lake = MockLake()
        lake.kg_chat = AsyncMock(return_value={
            "answer": "聚合根是一致性边界。",
            "retrieved_items": [{"name": "聚合根"}],
            "retrieval_count": 1,
        })
        client = TestClient(_make_app(lake))
        resp = client.post("/api/v1/kg/ask", json={"dataset": "jd_ddd", "question": "什么是聚合根"})
        assert resp.status_code == 200
        data = resp.json()
        assert "聚合根" in data["answer"]
        assert data["retrieval_count"] == 1
        lake.kg_chat.assert_awaited_once_with("jd_ddd", "什么是聚合根", top_k=5)
