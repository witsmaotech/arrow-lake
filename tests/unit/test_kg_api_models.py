"""Tests for knowledge graph API request/response models."""

from __future__ import annotations

import pytest
from arrow_lake.api.models.knowledge_graph import (
    GraphRAGQueryRequest,
    KGBuildRequest,
    KGBuildResponse,
    KGBuildStatusResponse,
    KGNeighborsResponse,
    KGQueryRequest,
    KGQueryResponse,
    KGSchemaResponse,
    KGStatsResponse,
)
from pydantic import ValidationError

# ---------------------------------------------------------------------------
# KGBuildRequest
# ---------------------------------------------------------------------------


class TestKGBuildRequest:
    def test_valid(self) -> None:
        req = KGBuildRequest(dataset_name="my-dataset_01")
        assert req.dataset_name == "my-dataset_01"

    def test_empty_name_rejected(self) -> None:
        with pytest.raises(ValidationError):
            KGBuildRequest(dataset_name="")

    def test_invalid_chars_rejected(self) -> None:
        with pytest.raises(ValidationError):
            KGBuildRequest(dataset_name="has spaces")

    def test_max_length(self) -> None:
        with pytest.raises(ValidationError):
            KGBuildRequest(dataset_name="a" * 257)

    def test_pattern_allows_hyphen_and_underscore(self) -> None:
        req = KGBuildRequest(dataset_name="my_dataset-v2")
        assert req.dataset_name == "my_dataset-v2"


# ---------------------------------------------------------------------------
# KGBuildResponse / KGBuildStatusResponse
# ---------------------------------------------------------------------------


class TestKGBuildResponses:
    def test_build_response(self) -> None:
        resp = KGBuildResponse(task_id="abc", status="pending", message="started")
        assert resp.task_id == "abc"

    def test_build_status_defaults(self) -> None:
        resp = KGBuildStatusResponse(
            task_id="t1", status="running", dataset_name="ds",
        )
        assert resp.total_chunks == 0
        assert resp.error is None


# ---------------------------------------------------------------------------
# KGQueryRequest
# ---------------------------------------------------------------------------


class TestKGQueryRequest:
    def test_valid(self) -> None:
        req = KGQueryRequest(gremlin="g.V().count()")
        assert req.gremlin == "g.V().count()"
        assert req.timeout_seconds == 30.0

    def test_custom_timeout(self) -> None:
        req = KGQueryRequest(gremlin="g.V()", timeout_seconds=60.0)
        assert req.timeout_seconds == 60.0

    def test_empty_gremlin_rejected(self) -> None:
        with pytest.raises(ValidationError):
            KGQueryRequest(gremlin="")

    def test_timeout_below_min(self) -> None:
        with pytest.raises(ValidationError):
            KGQueryRequest(gremlin="g.V()", timeout_seconds=0.5)

    def test_timeout_above_max(self) -> None:
        with pytest.raises(ValidationError):
            KGQueryRequest(gremlin="g.V()", timeout_seconds=301.0)


# ---------------------------------------------------------------------------
# KGSchemaResponse / KGStatsResponse / KGNeighborsResponse
# ---------------------------------------------------------------------------


class TestKGSchemaResponse:
    def test_default_empty(self) -> None:
        resp = KGSchemaResponse(vertex_labels=[], edge_labels=[])
        assert resp.vertex_labels == []
        assert resp.edge_labels == []


class TestKGStatsResponse:
    def test_fields(self) -> None:
        resp = KGStatsResponse(total_vertices=10, total_edges=20, graph_enabled=True)
        assert resp.total_vertices == 10
        assert resp.graph_enabled is True


class TestKGNeighborsResponse:
    def test_fields(self) -> None:
        resp = KGNeighborsResponse(
            center_id="v1", neighbors=[{"id": "v2"}], depth=2,
        )
        assert resp.center_id == "v1"
        assert len(resp.neighbors) == 1
        assert resp.depth == 2


class TestKGQueryResponse:
    def test_fields(self) -> None:
        resp = KGQueryResponse(results=[42], execution_time_ms=1.5)
        assert resp.results == [42]
        assert resp.execution_time_ms == 1.5


# ---------------------------------------------------------------------------
# GraphRAGQueryRequest
# ---------------------------------------------------------------------------


class TestGraphRAGQueryRequest:
    def test_valid(self) -> None:
        req = GraphRAGQueryRequest(question="What is X?", dataset_name="ds")
        assert req.top_k == 5
        assert req.traversal_depth == 2
        assert req.graph_weight == 0.3

    def test_custom_params(self) -> None:
        req = GraphRAGQueryRequest(
            question="test", dataset_name="ds", top_k=10, traversal_depth=3, graph_weight=0.7,
        )
        assert req.top_k == 10
        assert req.graph_weight == 0.7

    def test_empty_question_rejected(self) -> None:
        with pytest.raises(ValidationError):
            GraphRAGQueryRequest(question="", dataset_name="ds")

    def test_graph_weight_bounds(self) -> None:
        with pytest.raises(ValidationError):
            GraphRAGQueryRequest(question="q", dataset_name="ds", graph_weight=1.5)
