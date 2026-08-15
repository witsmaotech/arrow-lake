"""v1.10.5 M2 — every admin write operation lands in the audit trail, and the
shared API-key token exchange emits a throttled deprecation warning."""

from __future__ import annotations

import logging
from unittest.mock import MagicMock

import pytest
from httpx import ASGITransport, AsyncClient

from arrow_lake.api.app import create_app
from arrow_lake.config import ArrowLakeConfig
from arrow_lake.system_db import Migrator, SystemDB
from arrow_lake.system_db.stores import IdentityStore


@pytest.fixture
def mock_lake() -> MagicMock:
    return MagicMock()


@pytest.fixture
def identity() -> IdentityStore:
    db = SystemDB(":memory:")
    Migrator(db).run()
    yield IdentityStore(db)
    db.close()


@pytest.fixture
async def client(mock_lake: MagicMock, identity: IdentityStore) -> AsyncClient:
    from arrow_lake.api.rbac import PermissionChecker

    config = ArrowLakeConfig()
    config.api.api_key = "test-api-key"
    config.api.api_key_default_role = "ADMIN"
    config.api.docs_enabled = False
    config.redis.enabled = False
    app = create_app(config=config)
    app.state.lake = mock_lake
    app.state.identity_store = identity
    app.state.checker = PermissionChecker()
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
        headers={"X-API-Key": "test-api-key"},
    ) as ac:
        yield ac


def _events(mock_lake: MagicMock) -> list[tuple[str, dict]]:
    """Collected (event_type, payload) pairs from lake.audit_record calls."""
    out = []
    for call in mock_lake.audit_record.call_args_list:
        args, kwargs = call
        out.append((args[0] if args else kwargs.get("event_type", "?"), kwargs.get("payload", {})))
    return out


# ---------------------------------------------------------------------------
# M2: audit coverage on previously-unaudited admin write endpoints
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_update_user_audited(
    client: AsyncClient, mock_lake: MagicMock, identity: IdentityStore
) -> None:
    uid = identity.create_user("u1", role="viewer")
    r = await client.put(f"/api/v1/admin/users/{uid}", json={"email": "x@y.z", "password": "longpass-1"})
    assert r.status_code == 200
    events = _events(mock_lake)
    types = [t for t, _ in events]
    assert "security.user_updated" in types
    payload = next(p for t, p in events if t == "security.user_updated")
    assert payload["target_user_id"] == uid
    assert set(payload["fields"]) == {"email", "password"}


@pytest.mark.asyncio
async def test_schema_acl_set_and_delete_audited(
    client: AsyncClient, mock_lake: MagicMock
) -> None:
    r = await client.put(
        "/api/v1/admin/acl/schema/analytics",
        json={"role": "viewer", "allowed_actions": ["dataset:read"]},
    )
    assert r.status_code == 200
    r2 = await client.delete("/api/v1/admin/acl/schema/analytics/viewer")
    assert r2.status_code == 200
    schema_events = [
        p for t, p in _events(mock_lake) if t == "security.acl_changed"
    ]
    assert len(schema_events) == 2
    assert schema_events[0]["schema"] == "analytics"
    assert schema_events[0]["action"] == "set"
    assert schema_events[1]["action"] == "deleted"


@pytest.mark.asyncio
async def test_deny_remove_audited(client: AsyncClient, mock_lake: MagicMock) -> None:
    await client.put("/api/v1/admin/deny/ds1", json={"action": "dataset:delete"})
    mock_lake.audit_record.reset_mock()
    r = await client.delete("/api/v1/admin/deny/ds1/dataset:delete")
    assert r.status_code == 200
    events = _events(mock_lake)
    assert events and events[0][0] == "security.deny_changed"
    assert events[0][1]["dataset"] == "ds1"


# ---------------------------------------------------------------------------
# M2: shared API-key deprecation warning on /auth/token (throttled per IP+hour)
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_shared_key_exchange_warns_throttled(caplog) -> None:
    config = ArrowLakeConfig()
    config.auth.auth_mode = "both"
    config.auth.jwt_secret_key = "x" * 44
    config.api.api_key = "shared-key"
    config.api.docs_enabled = False
    config.redis.enabled = False
    app = create_app(config=config)
    app.state.lake = MagicMock()

    import arrow_lake.api.routers.auth as auth_router

    auth_router._SHARED_KEY_WARN.clear()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as ac:
        with caplog.at_level(logging.WARNING, logger="arrow_lake.api.routers.auth"):
            r1 = await ac.post("/api/v1/auth/token", headers={"X-API-Key": "shared-key"})
            r2 = await ac.post("/api/v1/auth/token", headers={"X-API-Key": "shared-key"})
        assert r1.status_code == 200 and r2.status_code == 200
        warnings = [r for r in caplog.records if "shared_api_key_deprecated" in r.message]
        assert len(warnings) == 1  # throttled: one per (ip, hour)
        assert "personal token" in warnings[0].message
    auth_router._SHARED_KEY_WARN.clear()
