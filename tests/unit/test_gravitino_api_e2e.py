"""End-to-end API tests for Gravitino /metadata/* endpoints.

Uses FastAPI TestClient with mocked Gravitino services injected via app.state.
Covers the full request chain: HTTP → router → bridge/service → response.
"""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from fastapi import FastAPI
from starlette.testclient import TestClient

from arrow_lake.api.auth_models import Role, TokenPayload
from arrow_lake.config.gravitino import GravitinoConfig
from arrow_lake.api.routers.gravitino import router as gravitino_router


@pytest.fixture
def app_with_gravitino() -> FastAPI:
    """Create a minimal FastAPI app with Gravitino router and mocked state."""
    app = FastAPI()
    app.state.config = MagicMock()
    app.state.config.gravitino = GravitinoConfig(
        enabled=True,
        uri="http://gravitino:8090",
        metalake="test-ml",
    )

    # Mock services on app.state (as lifespan would set them)
    bridge = MagicMock()
    tag_svc = MagicMock()
    model_reg = MagicMock()
    app.state.gravitino_bridge = bridge
    app.state.gravitino_tag_service = tag_svc
    app.state.gravitino_model_registry = model_reg

    app.include_router(gravitino_router)
    return app


@pytest.fixture
def client(app_with_gravitino: FastAPI) -> TestClient:
    """TestClient with an admin user injected via middleware bypass."""
    _admin = TokenPayload(sub="test-admin", role=Role.ADMIN, exp=0, iat=0)

    # Inject admin user on every request so require_role passes
    original_router = app_with_gravitino.router

    @app_with_gravitino.middleware("http")
    async def inject_admin_user(request, call_next):
        request.state.user = _admin
        return await call_next(request)

    return TestClient(app_with_gravitino)


@pytest.fixture
def app_without_gravitino() -> FastAPI:
    """App without Gravitino services (simulates disabled config)."""
    app = FastAPI()
    app.state.config = MagicMock()
    app.state.config.gravitino = GravitinoConfig(enabled=False)
    # No bridge/tag_service/model_registry on app.state
    app.include_router(gravitino_router)
    return app


# ---------------------------------------------------------------------------
# /metadata/catalogs
# ---------------------------------------------------------------------------


class TestCatalogEndpoints:
    @patch("arrow_lake.api.routers.gravitino._gravitino_get")
    def test_list_catalogs_success(self, mock_get: MagicMock, client: TestClient) -> None:
        mock_get.return_value = {
            "identifiers": [
                {"name": "lance-catalog"},
                {"name": "minio-fileset"},
            ],
        }
        resp = client.get("/metadata/catalogs")
        assert resp.status_code == 200
        body = resp.json()
        assert body["success"] is True
        assert len(body["data"]) == 2
        assert body["data"][0]["name"] == "lance-catalog"

    @patch("arrow_lake.api.routers.gravitino._gravitino_get")
    def test_list_catalogs_unreachable(self, mock_get: MagicMock, client: TestClient) -> None:
        mock_get.return_value = None
        resp = client.get("/metadata/catalogs")
        assert resp.status_code == 200
        body = resp.json()
        assert body["success"] is False
        assert body["error"] == "Gravitino unreachable"


# ---------------------------------------------------------------------------
# /metadata/tables
# ---------------------------------------------------------------------------


class TestTableEndpoints:
    @patch("arrow_lake.api.routers.gravitino._gravitino_get")
    def test_list_tables(self, mock_get: MagicMock, client: TestClient) -> None:
        mock_get.return_value = {
            "identifiers": [
                {"name": "documents"},
                {"name": "images"},
            ],
        }
        resp = client.get("/metadata/tables")
        assert resp.status_code == 200
        body = resp.json()
        assert body["success"] is True
        assert len(body["data"]) == 2

    @patch("arrow_lake.api.routers.gravitino._gravitino_get")
    def test_get_table_detail(self, mock_get: MagicMock, client: TestClient) -> None:
        mock_get.return_value = {
            "table": {
                "name": "documents",
                "columns": [{"name": "id", "type": "long"}],
                "properties": {"lance.latest_version": "5"},
            },
        }
        resp = client.get("/metadata/tables/documents")
        assert resp.status_code == 200
        body = resp.json()
        assert body["success"] is True
        assert body["data"]["name"] == "documents"
        assert len(body["data"]["columns"]) == 1

    @patch("arrow_lake.api.routers.gravitino._gravitino_get")
    def test_get_table_not_found(self, mock_get: MagicMock, client: TestClient) -> None:
        mock_get.return_value = None
        resp = client.get("/metadata/tables/nonexistent")
        assert resp.status_code == 200
        body = resp.json()
        assert body["success"] is False


# ---------------------------------------------------------------------------
# /metadata/tags
# ---------------------------------------------------------------------------


class TestTagEndpoints:
    def test_list_tags(self, client: TestClient) -> None:
        tag_svc = client.app.state.gravitino_tag_service
        tag_svc.list_tags.return_value = ["pii", "sensitive"]
        resp = client.get("/metadata/tags?table=test_table")
        assert resp.status_code == 200
        body = resp.json()
        assert body["success"] is True
        assert len(body["data"]) == 2

    def test_create_tag(self, client: TestClient) -> None:
        resp = client.post("/metadata/tags?body=%7B%22name%22%3A%22pii%22%2C%22comment%22%3A%22PII%22%7D")
        assert resp.status_code == 200
        body = resp.json()
        assert body["success"] is True
        assert body["data"]["name"] == "pii"

    def test_create_tag_missing_name(self, client: TestClient) -> None:
        resp = client.post("/metadata/tags?body=%7B%7D")
        assert resp.status_code == 400


# ---------------------------------------------------------------------------
# /metadata/policies
# ---------------------------------------------------------------------------


class TestPolicyEndpoints:
    @patch("arrow_lake.api.routers.gravitino._gravitino_get")
    def test_list_policies(self, mock_get: MagicMock, client: TestClient) -> None:
        mock_get.return_value = {"identifiers": [{"name": "retention_30d"}]}
        resp = client.get("/metadata/policies")
        assert resp.status_code == 200
        body = resp.json()
        assert body["success"] is True
        assert body["data"][0]["name"] == "retention_30d"

    def test_create_retention_policy_missing_name(self, client: TestClient) -> None:
        resp = client.post("/metadata/policies/retention?body=%7B%7D")
        assert resp.status_code == 400

    def test_create_masking_policy_missing_name(self, client: TestClient) -> None:
        resp = client.post("/metadata/policies/masking?body=%7B%7D")
        assert resp.status_code == 400


# ---------------------------------------------------------------------------
# /metadata/statistics
# ---------------------------------------------------------------------------


class TestStatisticsEndpoints:
    @patch("arrow_lake.catalog.gravitino_stats.GravitinoStatsCollector.collect_table_stats")
    @patch("arrow_lake.catalog.gravitino_stats.GravitinoStatsCollector.register_stats")
    def test_collect_stats(self, mock_register: MagicMock, mock_collect: MagicMock, client: TestClient) -> None:
        mock_collect.return_value = {"name": "test", "row_count": 42, "column_count": 3, "size_mb": 1.0, "columns": []}
        # Need a lake object for stats collection
        mock_lake = MagicMock()
        mock_lake._catalog._pool = MagicMock()
        client.app.state.lake = mock_lake

        resp = client.post("/metadata/statistics/test")
        assert resp.status_code == 200
        body = resp.json()
        assert body["success"] is True
        assert body["data"]["row_count"] == 42


# ---------------------------------------------------------------------------
# /metadata/models
# ---------------------------------------------------------------------------


class TestModelEndpoints:
    def test_list_models(self, client: TestClient) -> None:
        reg = client.app.state.gravitino_model_registry
        reg.list_models.return_value = ["bge-small-zh", "qwen2-7b"]
        resp = client.get("/metadata/models")
        assert resp.status_code == 200
        body = resp.json()
        assert body["success"] is True
        assert len(body["data"]) == 2

    def test_get_model_versions(self, client: TestClient) -> None:
        reg = client.app.state.gravitino_model_registry
        latest = MagicMock()
        latest.version = 2
        latest.uri = "s3://models/v2"
        latest.aliases = ["latest"]
        reg.get_latest_version.return_value = latest
        reg.get_production_version.return_value = None

        resp = client.get("/metadata/models/bge-small-zh/versions")
        assert resp.status_code == 200
        body = resp.json()
        assert body["success"] is True
        assert len(body["data"]) == 1
        assert body["data"][0]["version"] == 2


# ---------------------------------------------------------------------------
# 503 when Gravitino not configured
# ---------------------------------------------------------------------------


class TestDisabledGravitino:
    def test_tags_returns_503_when_disabled(self) -> None:
        app = FastAPI()
        app.state.config = MagicMock()
        app.state.config.gravitino = GravitinoConfig(enabled=False)
        app.include_router(gravitino_router)
        c = TestClient(app)

        resp = c.get("/metadata/tags")
        assert resp.status_code == 503

    def test_models_returns_503_when_disabled(self) -> None:
        app = FastAPI()
        app.state.config = MagicMock()
        app.state.config.gravitino = GravitinoConfig(enabled=False)
        app.include_router(gravitino_router)
        c = TestClient(app)

        resp = c.get("/metadata/models")
        assert resp.status_code == 503
