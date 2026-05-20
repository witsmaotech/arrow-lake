"""Tests for ACL management endpoints (admin)."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from arrow_lake.api.app import create_app
from arrow_lake.api.rbac import DatasetACL, PermissionChecker
from arrow_lake.config import ArrowLakeConfig
from httpx import ASGITransport, AsyncClient


@pytest.fixture
def checker() -> PermissionChecker:
    return PermissionChecker()


@pytest.fixture
def mock_lake() -> MagicMock:
    lake = MagicMock()
    lake.version.return_value = "1.4.0"
    return lake


@pytest.fixture
async def client(mock_lake: MagicMock, checker: PermissionChecker) -> AsyncClient:
    config = ArrowLakeConfig()
    config.api.api_key = "test-api-key"
    config.api.docs_enabled = False
    app = create_app(config=config)
    app.state.lake = mock_lake
    app.state.checker = checker
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
        headers={"X-API-Key": "test-api-key"},
    ) as ac:
        yield ac


# ---------------------------------------------------------------------------
# PUT /api/v1/admin/acl/{dataset}
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_set_acl(client: AsyncClient, checker: PermissionChecker) -> None:
    resp = await client.put(
        "/api/v1/admin/acl/my_dataset",
        json={
            "role": "viewer",
            "visible_columns": ["name", "age"],
            "row_filter": "age >= 18",
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["success"] is True
    assert body["dataset"] == "my_dataset"
    assert body["role"] == "viewer"

    acl = checker.get_acl("my_dataset", "viewer")
    assert acl is not None
    assert acl.visible_columns == frozenset({"name", "age"})
    assert acl.row_filter == "age >= 18"


@pytest.mark.asyncio
async def test_set_acl_column_only(client: AsyncClient, checker: PermissionChecker) -> None:
    resp = await client.put(
        "/api/v1/admin/acl/docs",
        json={"role": "viewer", "visible_columns": ["title"]},
    )
    assert resp.status_code == 200
    acl = checker.get_acl("docs", "viewer")
    assert acl is not None
    assert acl.visible_columns == frozenset({"title"})
    assert acl.row_filter == ""


@pytest.mark.asyncio
async def test_set_acl_row_only(client: AsyncClient, checker: PermissionChecker) -> None:
    resp = await client.put(
        "/api/v1/admin/acl/docs",
        json={"role": "editor", "row_filter": "region == US"},
    )
    assert resp.status_code == 200
    acl = checker.get_acl("docs", "editor")
    assert acl is not None
    assert acl.visible_columns == frozenset()
    assert acl.row_filter == "region == US"


@pytest.mark.asyncio
async def test_set_acl_overwrites(client: AsyncClient, checker: PermissionChecker) -> None:
    await client.put(
        "/api/v1/admin/acl/docs",
        json={"role": "viewer", "visible_columns": ["a"]},
    )
    await client.put(
        "/api/v1/admin/acl/docs",
        json={"role": "viewer", "visible_columns": ["b", "c"]},
    )
    acl = checker.get_acl("docs", "viewer")
    assert acl is not None
    assert acl.visible_columns == frozenset({"b", "c"})


@pytest.mark.asyncio
async def test_set_acl_invalid_role(client: AsyncClient) -> None:
    resp = await client.put(
        "/api/v1/admin/acl/docs",
        json={"role": "admin", "visible_columns": ["a"]},
    )
    assert resp.status_code == 422  # admin not allowed in ACL role


@pytest.mark.asyncio
async def test_set_acl_requires_auth(checker: PermissionChecker, mock_lake: MagicMock) -> None:
    config = ArrowLakeConfig()
    config.api.api_key = "test-api-key"
    config.api.docs_enabled = False
    app = create_app(config=config)
    app.state.lake = mock_lake
    app.state.checker = checker
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
    ) as ac:
        resp = await ac.put(
            "/api/v1/admin/acl/docs",
            json={"role": "viewer", "visible_columns": ["a"]},
        )
    assert resp.status_code in (401, 403)


# ---------------------------------------------------------------------------
# GET /api/v1/admin/acl/{dataset}
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_list_acls_empty(client: AsyncClient) -> None:
    resp = await client.get("/api/v1/admin/acl/docs")
    assert resp.status_code == 200
    body = resp.json()
    assert body["success"] is True
    assert body["acls"] == []


@pytest.mark.asyncio
async def test_list_acls_with_entries(client: AsyncClient, checker: PermissionChecker) -> None:
    checker.set_acl(DatasetACL(dataset="docs", role="viewer", visible_columns=frozenset({"a"})))
    checker.set_acl(DatasetACL(dataset="docs", role="editor", row_filter="x > 5"))
    resp = await client.get("/api/v1/admin/acl/docs")
    assert resp.status_code == 200
    body = resp.json()
    assert len(body["acls"]) == 2


# ---------------------------------------------------------------------------
# DELETE /api/v1/admin/acl/{dataset}/{role}
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_delete_acl(client: AsyncClient, checker: PermissionChecker) -> None:
    checker.set_acl(DatasetACL(dataset="docs", role="viewer", visible_columns=frozenset({"a"})))
    resp = await client.delete("/api/v1/admin/acl/docs/viewer")
    assert resp.status_code == 200
    body = resp.json()
    assert body["deleted"] is True
    assert checker.get_acl("docs", "viewer") is None


@pytest.mark.asyncio
async def test_delete_nonexistent_acl(client: AsyncClient) -> None:
    resp = await client.delete("/api/v1/admin/acl/docs/viewer")
    assert resp.status_code == 200
    body = resp.json()
    assert body["deleted"] is False


@pytest.mark.asyncio
async def test_delete_requires_auth(checker: PermissionChecker, mock_lake: MagicMock) -> None:
    config = ArrowLakeConfig()
    config.api.api_key = "test-api-key"
    config.api.docs_enabled = False
    app = create_app(config=config)
    app.state.lake = mock_lake
    app.state.checker = checker
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
    ) as ac:
        resp = await ac.delete("/api/v1/admin/acl/docs/viewer")
    assert resp.status_code in (401, 403)
