"""E2E tests for Knowledge Graph API endpoints.

Verifies the full API stack returns appropriate errors when KG is
disabled (default configuration).  No real HugeGraph instance is needed.
"""

from __future__ import annotations

import pytest
from arrow_lake.config import ArrowLakeConfig
from arrow_lake.exceptions import ErrorCode, KGError
from fastapi.testclient import TestClient


def _kg_disabled_error() -> KGError:
    """Return the standard KGError raised when KG is disabled."""
    return KGError(
        error_code=ErrorCode.KG_GRAPH_NOT_FOUND,
        message="Knowledge graph is not enabled. Set hugegraph.enabled=true in config.",
    )


def _create_app_with_kg_disabled() -> TestClient:
    """Create a test client with KG explicitly disabled.

    The Lake mock raises KGError(KG_GRAPH_NOT_FOUND) on every KG method
    call, mirroring the behavior of _ensure_kg_enabled().
    """
    from unittest.mock import AsyncMock, MagicMock

    from arrow_lake.api.app import create_app

    config = ArrowLakeConfig()
    config.hugegraph.enabled = False

    app = create_app(config=config)

    # Build a mock Lake whose KG methods raise KGError when awaited.
    # _get_kg_client() returns None (matching the disabled behavior).
    mock_lake = MagicMock()
    mock_lake._config = config
    mock_lake._get_kg_client.return_value = None

    error = _kg_disabled_error()

    async def _raise_kg_error(*args: object, **kwargs: object) -> object:
        raise error

    mock_lake.kg_build = AsyncMock(side_effect=_raise_kg_error)
    mock_lake.kg_build_status = AsyncMock(side_effect=_raise_kg_error)
    mock_lake.kg_query = AsyncMock(side_effect=_raise_kg_error)
    mock_lake.kg_get_neighbors = AsyncMock(side_effect=_raise_kg_error)
    mock_lake.kg_stats = AsyncMock(side_effect=_raise_kg_error)
    mock_lake.kg_delete_graph = AsyncMock(side_effect=_raise_kg_error)
    mock_lake.rag_query = AsyncMock(side_effect=_raise_kg_error)

    app.state.lake = mock_lake

    return TestClient(app, headers={"X-API-Key": "dev-api-key-for-local-testing-only"})


class TestKGAPIEndpoints:
    """E2E: KG endpoints return 404 when KG is disabled."""

    @pytest.fixture()
    def client(self) -> TestClient:
        return _create_app_with_kg_disabled()

    def test_build_returns_404_when_disabled(self, client: TestClient) -> None:
        """POST /api/v1/kg/build should return 404 when KG is not enabled."""
        resp = client.post(
            "/api/v1/kg/build",
            json={"dataset_name": "documents"},
        )
        assert resp.status_code == 404

    def test_build_status_returns_404_when_disabled(self, client: TestClient) -> None:
        """GET /api/v1/kg/build/{task_id}/status should return 404 when KG is not enabled."""
        resp = client.get("/api/v1/kg/build/nonexistent-task/status")
        assert resp.status_code == 404

    def test_stats_returns_404_when_disabled(self, client: TestClient) -> None:
        """GET /api/v1/kg/stats should return 404 when KG is not enabled."""
        resp = client.get("/api/v1/kg/stats")
        assert resp.status_code == 404

    def test_schema_returns_404_when_disabled(self, client: TestClient) -> None:
        """GET /api/v1/kg/schema should return 404 when KG is not enabled."""
        resp = client.get("/api/v1/kg/schema")
        assert resp.status_code == 404

    def test_query_returns_404_when_disabled(self, client: TestClient) -> None:
        """POST /api/v1/kg/query should return 404 when KG is not enabled."""
        resp = client.post(
            "/api/v1/kg/query",
            json={"gremlin": "g.V()"},
        )
        assert resp.status_code == 404

    def test_neighbors_returns_404_when_disabled(self, client: TestClient) -> None:
        """GET /api/v1/kg/entities/{entity_id}/neighbors should return 404 when KG is not enabled."""
        resp = client.get("/api/v1/kg/entities/some-entity/neighbors")
        assert resp.status_code == 404

    def test_delete_returns_404_when_disabled(self, client: TestClient) -> None:
        """DELETE /api/v1/kg/graph should return 404 when KG is not enabled."""
        resp = client.delete("/api/v1/kg/graph")
        assert resp.status_code == 404

    def test_graphrag_returns_404_when_disabled(self, client: TestClient) -> None:
        """POST /api/v1/kg/query/graphrag should return 404 when KG is not enabled."""
        resp = client.post(
            "/api/v1/kg/query/graphrag",
            json={"question": "What is Python?", "dataset_name": "documents"},
        )
        assert resp.status_code == 404

    def test_error_detail_includes_disabled_message(self, client: TestClient) -> None:
        """KG error responses should include a descriptive detail message."""
        resp = client.post(
            "/api/v1/kg/build",
            json={"dataset_name": "documents"},
        )
        assert resp.status_code == 404
        data = resp.json()
        assert "detail" in data
        assert "not enabled" in data["detail"].lower()
