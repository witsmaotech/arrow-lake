"""Tests for api/routers/gravitino.py — metadata proxy endpoints.

Covers: _validate_id, 503 not configured, catalogs/tables/tags/policies/
models/stats/enforce/lineage endpoints, _gravitino_get helper.
"""

from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from arrow_lake.api.app import create_app
from arrow_lake.api.auth_models import Role
from arrow_lake.api.rbac import PermissionChecker
from arrow_lake.config import ArrowLakeConfig


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _grav_config() -> SimpleNamespace:
    """Minimal Gravitino config object."""
    return SimpleNamespace(
        uri="http://gravitino:8090",
        metalake="arrow_lake",
    )


def _make_app(**state_overrides) -> FastAPI:
    """Create a test app with mocked Gravitino state."""
    config = ArrowLakeConfig()
    config.api.api_key = "test-api-key"
    config.api.api_key_default_role = "ADMIN"
    config.api.docs_enabled = False
    config.gravitino.enabled = True
    config.gravitino.uri = "http://gravitino:8090"
    config.gravitino.metalake = "arrow_lake"
    app = create_app(config=config)
    app.state.lake = MagicMock()
    app.state.checker = PermissionChecker()
    # Apply any extra state overrides
    for key, value in state_overrides.items():
        setattr(app.state, key, value)
    return app


@pytest.fixture
def grav_config_obj() -> SimpleNamespace:
    return _grav_config()


# ---------------------------------------------------------------------------
# _validate_id — rejects unsafe identifiers
# ---------------------------------------------------------------------------


class TestValidateId:
    """_validate_id raises 400 on invalid identifiers."""

    @pytest.mark.asyncio
    async def test_rejects_special_chars(self) -> None:
        app = _make_app()
        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test",
            headers={"X-API-Key": "test-api-key"},
        ) as ac:
            resp = await ac.get("/api/v1/metadata/tables/invalid name!")
        assert resp.status_code == 400

    @pytest.mark.asyncio
    async def test_rejects_dots_and_slashes(self) -> None:
        app = _make_app()
        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test",
            headers={"X-API-Key": "test-api-key"},
        ) as ac:
            resp = await ac.get("/api/v1/metadata/tables/a.b")
        assert resp.status_code == 400


# ---------------------------------------------------------------------------
# 503 — Gravitino not configured
# ---------------------------------------------------------------------------


class TestServiceNotConfigured:
    """Endpoints return 503 when Gravitino services are not on app.state."""

    @pytest.mark.asyncio
    async def test_tags_503_without_service(self) -> None:
        app = _make_app()  # no gravitino_tag_service
        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test",
            headers={"X-API-Key": "test-api-key"},
        ) as ac:
            resp = await ac.get("/api/v1/metadata/tags")
        assert resp.status_code == 503

    @pytest.mark.asyncio
    async def test_models_503_without_registry(self) -> None:
        app = _make_app()  # no gravitino_model_registry
        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test",
            headers={"X-API-Key": "test-api-key"},
        ) as ac:
            resp = await ac.get("/api/v1/metadata/models")
        assert resp.status_code == 503


# ---------------------------------------------------------------------------
# GET /metadata/catalogs
# ---------------------------------------------------------------------------


class TestListCatalogs:
    """GET /metadata/catalogs — list Gravitino catalogs."""

    @pytest.mark.asyncio
    async def test_returns_catalogs(self) -> None:
        mock_resp = json.dumps({
            "identifiers": [
                {"name": "lance-catalog", "namespace": []},
                {"name": "minio-fileset", "namespace": []},
            ],
        }).encode()

        app = _make_app()
        with patch("arrow_lake.api.routers.gravitino.urlopen") as mock_urlopen:
            mock_cm = MagicMock()
            mock_cm.__enter__ = MagicMock(return_value=mock_cm)
            mock_cm.__exit__ = MagicMock(return_value=False)
            mock_cm.read.return_value = mock_resp
            mock_urlopen.return_value = mock_cm

            async with AsyncClient(
                transport=ASGITransport(app=app),
                base_url="http://test",
                headers={"X-API-Key": "test-api-key"},
            ) as ac:
                resp = await ac.get("/api/v1/metadata/catalogs")

        assert resp.status_code == 200
        body = resp.json()
        assert body["success"] is True
        assert body["metadata"]["total"] == 2

    @pytest.mark.asyncio
    async def test_returns_error_when_unreachable(self) -> None:
        app = _make_app()
        with patch("arrow_lake.api.routers.gravitino.urlopen", side_effect=Exception("conn refused")):
            async with AsyncClient(
                transport=ASGITransport(app=app),
                base_url="http://test",
                headers={"X-API-Key": "test-api-key"},
            ) as ac:
                resp = await ac.get("/api/v1/metadata/catalogs")

        assert resp.status_code == 200
        body = resp.json()
        assert body["success"] is False
        assert body["error"] == "Gravitino unreachable"


# ---------------------------------------------------------------------------
# GET /metadata/tables
# ---------------------------------------------------------------------------


class TestListTables:
    """GET /metadata/tables — list tables in lance-catalog."""

    @pytest.mark.asyncio
    async def test_returns_tables(self) -> None:
        mock_resp = json.dumps({
            "identifiers": [{"name": "docs"}, {"name": "images"}],
        }).encode()

        app = _make_app()
        with patch("arrow_lake.api.routers.gravitino.urlopen") as mock_urlopen:
            mock_cm = MagicMock()
            mock_cm.__enter__ = MagicMock(return_value=mock_cm)
            mock_cm.__exit__ = MagicMock(return_value=False)
            mock_cm.read.return_value = mock_resp
            mock_urlopen.return_value = mock_cm

            async with AsyncClient(
                transport=ASGITransport(app=app),
                base_url="http://test",
                headers={"X-API-Key": "test-api-key"},
            ) as ac:
                resp = await ac.get("/api/v1/metadata/tables")

        assert resp.status_code == 200
        body = resp.json()
        assert body["success"] is True
        assert body["metadata"]["total"] == 2


# ---------------------------------------------------------------------------
# GET /metadata/tables/{name}
# ---------------------------------------------------------------------------


class TestGetTable:
    """GET /metadata/tables/{name} — table details with columns."""

    @pytest.mark.asyncio
    async def test_returns_table_details(self) -> None:
        mock_resp = json.dumps({
            "table": {
                "name": "docs",
                "columns": [{"name": "id", "type": "integer"}],
                "properties": {"format": "lance"},
            },
        }).encode()

        app = _make_app()
        with patch("arrow_lake.api.routers.gravitino.urlopen") as mock_urlopen:
            mock_cm = MagicMock()
            mock_cm.__enter__ = MagicMock(return_value=mock_cm)
            mock_cm.__exit__ = MagicMock(return_value=False)
            mock_cm.read.return_value = mock_resp
            mock_urlopen.return_value = mock_cm

            async with AsyncClient(
                transport=ASGITransport(app=app),
                base_url="http://test",
                headers={"X-API-Key": "test-api-key"},
            ) as ac:
                resp = await ac.get("/api/v1/metadata/tables/docs")

        assert resp.status_code == 200
        body = resp.json()
        assert body["success"] is True
        assert body["data"]["name"] == "docs"
        assert len(body["data"]["columns"]) == 1

    @pytest.mark.asyncio
    async def test_returns_not_found_when_unreachable(self) -> None:
        app = _make_app()
        # Simulate a truly-missing dataset so the lake fallback also misses.
        app.state.lake.open_dataset.side_effect = Exception("not found")
        with patch("arrow_lake.api.routers.gravitino.urlopen", side_effect=Exception("err")):
            async with AsyncClient(
                transport=ASGITransport(app=app),
                base_url="http://test",
                headers={"X-API-Key": "test-api-key"},
            ) as ac:
                resp = await ac.get("/api/v1/metadata/tables/missing")

        assert resp.status_code == 200
        body = resp.json()
        assert body["success"] is False
        assert body["error"] == "Table not found"


# ---------------------------------------------------------------------------
# GET /metadata/tags
# ---------------------------------------------------------------------------


class TestListTags:
    """GET /metadata/tags — list tags via tag service."""

    @pytest.mark.asyncio
    async def test_returns_tags(self) -> None:
        tag_svc = MagicMock()
        tag_svc.list_tags.return_value = ["pii", "sensitive"]
        app = _make_app(gravitino_tag_service=tag_svc)

        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test",
            headers={"X-API-Key": "test-api-key"},
        ) as ac:
            resp = await ac.get("/api/v1/metadata/tags?table=docs")

        assert resp.status_code == 200
        body = resp.json()
        assert body["success"] is True
        assert body["metadata"]["total"] == 2
        tag_svc.list_tags.assert_called_once_with("docs")

    @pytest.mark.asyncio
    async def test_returns_error_on_exception(self) -> None:
        tag_svc = MagicMock()
        tag_svc.list_tags.side_effect = RuntimeError("service down")
        app = _make_app(gravitino_tag_service=tag_svc)

        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test",
            headers={"X-API-Key": "test-api-key"},
        ) as ac:
            resp = await ac.get("/api/v1/metadata/tags?table=docs")

        assert resp.status_code == 200
        body = resp.json()
        assert body["success"] is False


# ---------------------------------------------------------------------------
# POST /metadata/tags
# ---------------------------------------------------------------------------


class TestCreateTag:
    """POST /metadata/tags — create a new tag."""

    @pytest.mark.asyncio
    async def test_creates_tag(self) -> None:
        tag_svc = MagicMock()
        app = _make_app(gravitino_tag_service=tag_svc)

        body_json = json.dumps({"name": "pii", "comment": "PII data"})
        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test",
            headers={"X-API-Key": "test-api-key"},
        ) as ac:
            resp = await ac.post("/api/v1/metadata/tags", json=json.loads(body_json))

        assert resp.status_code == 200
        body = resp.json()
        assert body["success"] is True
        assert body["data"]["name"] == "pii"
        tag_svc.create_tag.assert_called_once_with("pii", "PII data")

    @pytest.mark.asyncio
    async def test_rejects_empty_name(self) -> None:
        tag_svc = MagicMock()
        app = _make_app(gravitino_tag_service=tag_svc)

        body_json = json.dumps({"name": "", "comment": ""})
        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test",
            headers={"X-API-Key": "test-api-key"},
        ) as ac:
            resp = await ac.post("/api/v1/metadata/tags", json=json.loads(body_json))

        assert resp.status_code == 400

    @pytest.mark.asyncio
    async def test_rejects_invalid_json(self) -> None:
        tag_svc = MagicMock()
        app = _make_app(gravitino_tag_service=tag_svc)

        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test",
            headers={"X-API-Key": "test-api-key"},
        ) as ac:
            resp = await ac.post("/api/v1/metadata/tags?body=not-json")

        assert resp.status_code == 400


# ---------------------------------------------------------------------------
# GET /metadata/policies
# ---------------------------------------------------------------------------


class TestListPolicies:
    """GET /metadata/policies — list Gravitino policies."""

    @pytest.mark.asyncio
    async def test_returns_policies(self) -> None:
        mock_resp = json.dumps({
            "identifiers": [{"name": "retention-30d"}],
        }).encode()

        app = _make_app()
        with patch("arrow_lake.api.routers.gravitino.urlopen") as mock_urlopen:
            mock_cm = MagicMock()
            mock_cm.__enter__ = MagicMock(return_value=mock_cm)
            mock_cm.__exit__ = MagicMock(return_value=False)
            mock_cm.read.return_value = mock_resp
            mock_urlopen.return_value = mock_cm

            async with AsyncClient(
                transport=ASGITransport(app=app),
                base_url="http://test",
                headers={"X-API-Key": "test-api-key"},
            ) as ac:
                resp = await ac.get("/api/v1/metadata/policies")

        assert resp.status_code == 200
        body = resp.json()
        assert body["success"] is True
        assert body["metadata"]["total"] == 1


# ---------------------------------------------------------------------------
# POST /metadata/policies/retention
# ---------------------------------------------------------------------------


class TestCreateRetentionPolicy:
    """POST /metadata/policies/retention — create retention policy."""

    @pytest.mark.asyncio
    async def test_creates_policy(self) -> None:
        app = _make_app()

        with patch(
            "arrow_lake.api.routers.gravitino.GravitinoPolicyService",
            create=True,
        ) as MockSvc:
            mock_svc = MagicMock()
            MockSvc.return_value = mock_svc

            body_json = json.dumps({"name": "ret-30d", "days": 30})
            async with AsyncClient(
                transport=ASGITransport(app=app),
                base_url="http://test",
                headers={"X-API-Key": "test-api-key"},
            ) as ac:
                resp = await ac.post("/api/v1/metadata/policies/retention", json=json.loads(body_json))

        assert resp.status_code == 200
        body = resp.json()
        assert body["success"] is True
        assert body["data"]["days"] == 30

    @pytest.mark.asyncio
    async def test_rejects_empty_name(self) -> None:
        app = _make_app()

        body_json = json.dumps({"name": "", "days": 30})
        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test",
            headers={"X-API-Key": "test-api-key"},
        ) as ac:
            resp = await ac.post("/api/v1/metadata/policies/retention", json=json.loads(body_json))

        assert resp.status_code == 400


# ---------------------------------------------------------------------------
# POST /metadata/policies/masking
# ---------------------------------------------------------------------------


class TestCreateMaskingPolicy:
    """POST /metadata/policies/masking — create masking policy."""

    @pytest.mark.asyncio
    async def test_creates_policy(self) -> None:
        app = _make_app()

        with patch(
            "arrow_lake.api.routers.gravitino.GravitinoPolicyService",
            create=True,
        ) as MockSvc:
            mock_svc = MagicMock()
            MockSvc.return_value = mock_svc

            body_json = json.dumps({"name": "mask-email", "columns": ["email"]})
            async with AsyncClient(
                transport=ASGITransport(app=app),
                base_url="http://test",
                headers={"X-API-Key": "test-api-key"},
            ) as ac:
                resp = await ac.post("/api/v1/metadata/policies/masking", json=json.loads(body_json))

        assert resp.status_code == 200
        body = resp.json()
        assert body["success"] is True
        assert "email" in body["data"]["columns"]


# ---------------------------------------------------------------------------
# POST /metadata/statistics/{name}
# ---------------------------------------------------------------------------


class TestCollectStats:
    """POST /metadata/statistics/{name} — collect table stats."""

    @pytest.mark.asyncio
    async def test_collects_and_registers(self) -> None:
        mock_pool = MagicMock()
        mock_lake = MagicMock()
        mock_lake._catalog._pool = mock_pool
        app = _make_app()
        app.state.lake = mock_lake

        mock_collector = MagicMock()
        mock_collector.collect_table_stats.return_value = {"row_count": 100}

        with patch(
            "arrow_lake.catalog.gravitino_stats.GravitinoStatsCollector",
            return_value=mock_collector,
        ):
            async with AsyncClient(
                transport=ASGITransport(app=app),
                base_url="http://test",
                headers={"X-API-Key": "test-api-key"},
            ) as ac:
                resp = await ac.post("/api/v1/metadata/statistics/docs")

        assert resp.status_code == 200
        body = resp.json()
        assert body["success"] is True
        assert body["data"]["row_count"] == 100


# ---------------------------------------------------------------------------
# GET /metadata/models
# ---------------------------------------------------------------------------


class TestListModels:
    """GET /metadata/models — list registered models."""

    @pytest.mark.asyncio
    async def test_returns_models(self) -> None:
        registry = MagicMock()
        registry.list_models.return_value = ["sentiment-v1", "ner-v2"]
        app = _make_app(gravitino_model_registry=registry)

        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test",
            headers={"X-API-Key": "test-api-key"},
        ) as ac:
            resp = await ac.get("/api/v1/metadata/models")

        assert resp.status_code == 200
        body = resp.json()
        assert body["success"] is True
        assert body["metadata"]["total"] == 2

    @pytest.mark.asyncio
    async def test_returns_error_on_exception(self) -> None:
        registry = MagicMock()
        registry.list_models.side_effect = RuntimeError("down")
        app = _make_app(gravitino_model_registry=registry)

        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test",
            headers={"X-API-Key": "test-api-key"},
        ) as ac:
            resp = await ac.get("/api/v1/metadata/models")

        assert resp.status_code == 200
        body = resp.json()
        assert body["success"] is False


# ---------------------------------------------------------------------------
# GET /metadata/models/{name}/versions
# ---------------------------------------------------------------------------


class TestGetModelVersions:
    """GET /metadata/models/{name}/versions — model version info."""

    @pytest.mark.asyncio
    async def test_returns_versions(self) -> None:
        latest = SimpleNamespace(version=2, uri="s3://m/v2", aliases=["latest"])
        production = SimpleNamespace(version=1, uri="s3://m/v1", aliases=["production"])
        registry = MagicMock()
        registry.get_latest_version.return_value = latest
        registry.get_production_version.return_value = production
        app = _make_app(gravitino_model_registry=registry)

        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test",
            headers={"X-API-Key": "test-api-key"},
        ) as ac:
            resp = await ac.get("/api/v1/metadata/models/sentinel/versions")

        assert resp.status_code == 200
        body = resp.json()
        assert body["success"] is True
        assert body["metadata"]["model"] == "sentinel"
        assert len(body["data"]) == 2

    @pytest.mark.asyncio
    async def test_returns_latest_only_when_same_as_production(self) -> None:
        same = SimpleNamespace(version=2, uri="s3://m/v2", aliases=["latest"])
        registry = MagicMock()
        registry.get_latest_version.return_value = same
        registry.get_production_version.return_value = same
        app = _make_app(gravitino_model_registry=registry)

        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test",
            headers={"X-API-Key": "test-api-key"},
        ) as ac:
            resp = await ac.get("/api/v1/metadata/models/model-a/versions")

        assert resp.status_code == 200
        body = resp.json()
        assert len(body["data"]) == 1
        assert body["data"][0]["tier"] == "latest"

    @pytest.mark.asyncio
    async def test_returns_empty_when_no_versions(self) -> None:
        registry = MagicMock()
        registry.get_latest_version.return_value = None
        registry.get_production_version.return_value = None
        app = _make_app(gravitino_model_registry=registry)

        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test",
            headers={"X-API-Key": "test-api-key"},
        ) as ac:
            resp = await ac.get("/api/v1/metadata/models/model-b/versions")

        assert resp.status_code == 200
        body = resp.json()
        assert body["data"] == []


# ---------------------------------------------------------------------------
# POST /metadata/policies/enforce
# ---------------------------------------------------------------------------


class TestEnforcePolicies:
    """POST /metadata/policies/enforce — trigger policy enforcement."""

    @pytest.mark.asyncio
    async def test_enforce_all(self) -> None:
        enforcer = MagicMock()
        enforcer.enforce.return_value = 5
        app = _make_app(retention_enforcer=enforcer)

        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test",
            headers={"X-API-Key": "test-api-key"},
        ) as ac:
            resp = await ac.post("/api/v1/metadata/policies/enforce")

        assert resp.status_code == 200
        body = resp.json()
        assert body["success"] is True
        assert body["data"]["tables_cleaned"] == 5
        assert body["data"]["dry_run"] is False

    @pytest.mark.asyncio
    async def test_enforce_specific_table_dry_run(self) -> None:
        enforcer = MagicMock()
        enforcer.enforce_table.return_value = 1
        app = _make_app(retention_enforcer=enforcer)

        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test",
            headers={"X-API-Key": "test-api-key"},
        ) as ac:
            resp = await ac.post("/api/v1/metadata/policies/enforce?table=docs&dry_run=true")

        assert resp.status_code == 200
        body = resp.json()
        assert body["data"]["dry_run"] is True
        enforcer.enforce_table.assert_called_once_with("docs", dry_run=True)

    @pytest.mark.asyncio
    async def test_returns_503_without_enforcer(self) -> None:
        app = _make_app()  # no retention_enforcer

        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test",
            headers={"X-API-Key": "test-api-key"},
        ) as ac:
            resp = await ac.post("/api/v1/metadata/policies/enforce")

        assert resp.status_code == 503


# ---------------------------------------------------------------------------
# GET /metadata/lineage/{name}
# ---------------------------------------------------------------------------


class TestGetLineage:
    """GET /metadata/lineage/{name} — lineage from Gravitino properties."""

    @pytest.mark.asyncio
    async def test_returns_lineage(self) -> None:
        mock_resp = json.dumps({
            "table": {
                "name": "docs",
                "properties": {
                    "lineage.sources": '["raw_data"]',
                    "lineage.outputs": '["report"]',
                    "lineage.operation": "transform",
                    "lineage.timestamp": "2026-01-01",
                    "lance.latest_version": "42",
                },
            },
        }).encode()

        app = _make_app()
        with patch("arrow_lake.api.routers.gravitino.urlopen") as mock_urlopen:
            mock_cm = MagicMock()
            mock_cm.__enter__ = MagicMock(return_value=mock_cm)
            mock_cm.__exit__ = MagicMock(return_value=False)
            mock_cm.read.return_value = mock_resp
            mock_urlopen.return_value = mock_cm

            async with AsyncClient(
                transport=ASGITransport(app=app),
                base_url="http://test",
                headers={"X-API-Key": "test-api-key"},
            ) as ac:
                resp = await ac.get("/api/v1/metadata/lineage/docs")

        assert resp.status_code == 200
        body = resp.json()
        assert body["success"] is True
        assert body["data"]["table"] == "docs"
        assert body["data"]["sources"] == ["raw_data"]
        assert body["data"]["outputs"] == ["report"]
        assert body["data"]["lance_version"] == "42"

    @pytest.mark.asyncio
    async def test_returns_not_found_when_unreachable(self) -> None:
        app = _make_app()
        with patch("arrow_lake.api.routers.gravitino.urlopen", side_effect=Exception("err")):
            async with AsyncClient(
                transport=ASGITransport(app=app),
                base_url="http://test",
                headers={"X-API-Key": "test-api-key"},
            ) as ac:
                resp = await ac.get("/api/v1/metadata/lineage/missing")

        assert resp.status_code == 200
        body = resp.json()
        assert body["success"] is False

    @pytest.mark.asyncio
    async def test_handles_invalid_json_in_properties(self) -> None:
        mock_resp = json.dumps({
            "table": {
                "name": "docs",
                "properties": {
                    "lineage.sources": "not-json",
                    "lineage.outputs": "also-bad",
                },
            },
        }).encode()

        app = _make_app()
        with patch("arrow_lake.api.routers.gravitino.urlopen") as mock_urlopen:
            mock_cm = MagicMock()
            mock_cm.__enter__ = MagicMock(return_value=mock_cm)
            mock_cm.__exit__ = MagicMock(return_value=False)
            mock_cm.read.return_value = mock_resp
            mock_urlopen.return_value = mock_cm

            async with AsyncClient(
                transport=ASGITransport(app=app),
                base_url="http://test",
                headers={"X-API-Key": "test-api-key"},
            ) as ac:
                resp = await ac.get("/api/v1/metadata/lineage/docs")

        assert resp.status_code == 200
        body = resp.json()
        assert body["data"]["sources"] == []
        assert body["data"]["outputs"] == []
