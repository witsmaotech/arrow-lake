"""Auth contract tests for gravitino metadata endpoints — v1.9.4 批0.

Closes the Bearer-passthrough gap: ``api_key_middleware`` forwards Bearer
requests to downstream deps (auth.py:100-101), so endpoints without
``require_role`` ran unauthenticated for any ``Authorization: Bearer ...``
header. Both tables endpoints now gate on ``require_role(Role.VIEWER)``.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

from arrow_lake.api.app import create_app
from arrow_lake.api.rbac import PermissionChecker
from arrow_lake.config import ArrowLakeConfig


def _make_app() -> object:
    config = ArrowLakeConfig()
    config.api.api_key = "test-api-key"
    config.api.api_key_default_role = "ADMIN"
    config.api.docs_enabled = False
    config.gravitino.enabled = True
    config.gravitino.uri = "http://gravitino:8090"
    config.gravitino.metalake = "arrow_lake"
    app = create_app(config=config)
    lake = MagicMock()
    lake.list_datasets.return_value = []
    app.state.lake = lake
    app.state.checker = PermissionChecker()
    return app


class TestGravitinoTablesAuth:
    """v1.9.4 批0: /metadata/tables endpoints enforce authentication + RBAC."""

    @pytest.mark.asyncio
    async def test_tables_no_credentials_rejected(self) -> None:
        app = _make_app()
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            resp = await ac.get("/api/v1/metadata/tables")
        assert resp.status_code in (401, 403)

    @pytest.mark.asyncio
    async def test_table_by_name_bearer_junk_rejected(self) -> None:
        # Bearer headers are passed through by api_key_middleware; require_role
        # must still reject when no valid user is established.
        app = _make_app()
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            resp = await ac.get("/api/v1/metadata/tables/foo", headers={"Authorization": "Bearer junk"})
        assert resp.status_code in (401, 403)

    @pytest.mark.asyncio
    async def test_tables_bearer_junk_rejected(self) -> None:
        app = _make_app()
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            resp = await ac.get("/api/v1/metadata/tables", headers={"Authorization": "Bearer junk"})
        assert resp.status_code in (401, 403)

    @pytest.mark.asyncio
    async def test_tables_valid_api_key_passes_auth(self) -> None:
        app = _make_app()
        with patch("arrow_lake.api.routers.gravitino._gravitino_get", return_value={"identifiers": []}):
            async with AsyncClient(
                transport=ASGITransport(app=app),
                base_url="http://test",
                headers={"X-API-Key": "test-api-key"},
            ) as ac:
                resp = await ac.get("/api/v1/metadata/tables")
        assert resp.status_code == 200
        assert resp.json()["success"] is True
