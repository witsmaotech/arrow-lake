"""Tests for admin router: list_users, ACL CRUD endpoints."""

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
    return MagicMock()


@pytest.fixture
async def client(mock_lake: MagicMock, checker: PermissionChecker) -> AsyncClient:
    config = ArrowLakeConfig()
    config.api.api_key = "test-api-key"
    config.api.api_key_default_role = "ADMIN"
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
# GET /api/v1/admin/users
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_list_users(client: AsyncClient) -> None:
    resp = await client.get("/api/v1/admin/users")
    assert resp.status_code == 200
    body = resp.json()
    assert "users" in body
    assert isinstance(body["users"], list)


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
async def test_set_acl_overwrites(client: AsyncClient, checker: PermissionChecker) -> None:
    await client.put(
        "/api/v1/admin/acl/ds1",
        json={"role": "viewer", "visible_columns": ["a"]},
    )
    await client.put(
        "/api/v1/admin/acl/ds1",
        json={"role": "viewer", "visible_columns": ["b", "c"]},
    )
    acl = checker.get_acl("ds1", "viewer")
    assert acl is not None
    assert acl.visible_columns == frozenset({"b", "c"})


# ---------------------------------------------------------------------------
# GET /api/v1/admin/acl/{dataset}
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_list_acls_empty(client: AsyncClient) -> None:
    resp = await client.get("/api/v1/admin/acl/nonexistent")
    assert resp.status_code == 200
    body = resp.json()
    assert body["success"] is True
    assert body["acls"] == []


@pytest.mark.asyncio
async def test_list_acls_multiple(client: AsyncClient, checker: PermissionChecker) -> None:
    checker.set_acl(DatasetACL(dataset="ds", role="viewer", visible_columns=frozenset({"a"})))
    checker.set_acl(DatasetACL(dataset="ds", role="editor", row_filter="x > 5"))

    resp = await client.get("/api/v1/admin/acl/ds")
    assert resp.status_code == 200
    body = resp.json()
    assert len(body["acls"]) == 2
    roles = {a["role"] for a in body["acls"]}
    assert roles == {"viewer", "editor"}


# ---------------------------------------------------------------------------
# DELETE /api/v1/admin/acl/{dataset}/{role}
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_delete_acl(client: AsyncClient, checker: PermissionChecker) -> None:
    checker.set_acl(DatasetACL(dataset="ds", role="viewer"))

    resp = await client.delete("/api/v1/admin/acl/ds/viewer")
    assert resp.status_code == 200
    body = resp.json()
    assert body["deleted"] is True
    assert checker.get_acl("ds", "viewer") is None


@pytest.mark.asyncio
async def test_delete_acl_not_found(client: AsyncClient) -> None:
    resp = await client.delete("/api/v1/admin/acl/ds/viewer")
    assert resp.status_code == 200
    body = resp.json()
    assert body["deleted"] is False


# ---------------------------------------------------------------------------
# Auth required
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_admin_requires_auth(mock_lake: MagicMock) -> None:
    config = ArrowLakeConfig()
    config.api.api_key = "test-api-key"
    config.api.docs_enabled = False
    app = create_app(config=config)
    app.state.lake = mock_lake

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
    ) as ac:
        resp = await ac.get("/api/v1/admin/users")
    assert resp.status_code in (401, 403)


@pytest.mark.asyncio
async def test_admin_requires_admin_role(mock_lake: MagicMock) -> None:
    config = ArrowLakeConfig()
    config.api.api_key = "test-api-key"
    config.api.api_key_default_role = "VIEWER"
    config.api.docs_enabled = False
    app = create_app(config=config)
    app.state.lake = mock_lake

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
        headers={"X-API-Key": "test-api-key"},
    ) as ac:
        resp = await ac.get("/api/v1/admin/users")
    assert resp.status_code == 403


# ---------------------------------------------------------------------------
# v1.9.1: roles catalog + user update/deactivate + personal tokens
# ---------------------------------------------------------------------------
@pytest.fixture
def identity_store() -> MagicMock:
    m = MagicMock()
    m.list_users.return_value = [
        {"id": 1, "username": "u", "email": None, "role": "viewer",
         "is_active": True, "created_at": "", "updated_at": ""}
    ]
    m.update_user.return_value = True
    m.validate_token.return_value = None  # X-API-Key 不是 personal token → fall through 共享 api_key 路径
    m.create_token.return_value = (
        "al_secretTOKEN123",
        {"id": 10, "user_id": 1, "name": "ci", "token_prefix": "al_secr",
         "scopes": ["dataset:read"], "expires_at": None},
    )
    m.list_tokens.return_value = [
        {"id": 10, "name": "ci", "token_prefix": "al_secr", "scopes": None,
         "expires_at": None, "last_used_at": None, "created_at": "", "revoked_at": None}
    ]
    m.revoke_token.return_value = True
    return m


@pytest.fixture
async def admin_client(identity_store: MagicMock) -> AsyncClient:
    config = ArrowLakeConfig()
    config.api.api_key = "test-api-key"
    config.api.api_key_default_role = "ADMIN"
    config.api.docs_enabled = False
    app = create_app(config=config)
    app.state.lake = MagicMock()
    app.state.checker = PermissionChecker()
    app.state.identity_store = identity_store
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
        headers={"X-API-Key": "test-api-key"},
    ) as ac:
        yield ac


@pytest.mark.asyncio
async def test_list_roles(admin_client: AsyncClient) -> None:
    resp = await admin_client.get("/api/v1/admin/roles")
    assert resp.status_code == 200
    assert [r["name"] for r in resp.json()["roles"]] == ["admin", "editor", "viewer"]


@pytest.mark.asyncio
async def test_update_user(admin_client: AsyncClient, identity_store: MagicMock) -> None:
    resp = await admin_client.put(
        "/api/v1/admin/users/1", json={"role": "editor", "is_active": False}
    )
    assert resp.status_code == 200
    assert resp.json() == {"id": 1, "updated": True}
    identity_store.update_user.assert_called_once()


@pytest.mark.asyncio
async def test_update_user_no_fields_422(admin_client: AsyncClient) -> None:
    resp = await admin_client.put("/api/v1/admin/users/1", json={})
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_deactivate_user(admin_client: AsyncClient, identity_store: MagicMock) -> None:
    resp = await admin_client.delete("/api/v1/admin/users/1")
    assert resp.status_code == 200
    assert resp.json()["deactivated"] is True
    identity_store.set_user_active.assert_called_once_with(1, False)


@pytest.mark.asyncio
async def test_issue_token_returns_plaintext_once(
    admin_client: AsyncClient, identity_store: MagicMock
) -> None:
    resp = await admin_client.post(
        "/api/v1/admin/users/1/tokens", json={"name": "ci", "scopes": ["dataset:read"]}
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["token"] == "al_secretTOKEN123"  # plaintext returned once
    assert body["token_prefix"] == "al_secr"
    identity_store.create_token.assert_called_once()


@pytest.mark.asyncio
async def test_list_user_tokens(admin_client: AsyncClient) -> None:
    resp = await admin_client.get("/api/v1/admin/users/1/tokens")
    assert resp.status_code == 200
    assert len(resp.json()["tokens"]) == 1


@pytest.mark.asyncio
async def test_revoke_token(admin_client: AsyncClient, identity_store: MagicMock) -> None:
    resp = await admin_client.delete("/api/v1/admin/users/1/tokens/10")
    assert resp.status_code == 200
    assert resp.json()["revoked"] is True
    identity_store.revoke_token.assert_called_once_with(10)
