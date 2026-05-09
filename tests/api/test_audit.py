"""Tests for audit trail endpoints."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from arrow_lake.api.app import create_app
from arrow_lake.config import ArrowLakeConfig
from httpx import ASGITransport, AsyncClient


@pytest.fixture
def mock_lake() -> MagicMock:
    lake = MagicMock()
    lake.audit_record.return_value = "audit-abc-123"
    lake.audit_verify.return_value = True
    lake.audit_query.return_value = [
        {"audit_id": "audit-1", "event_type": "ingest", "dataset_name": "docs"},
        {"audit_id": "audit-2", "event_type": "delete", "dataset_name": "old"},
    ]
    lake.audit_export.return_value = {
        "dataset_name": "docs",
        "total_entries": 2,
        "format": "json",
    }
    return lake


@pytest.fixture
async def client(mock_lake: MagicMock) -> AsyncClient:
    config = ArrowLakeConfig()
    config.api.api_key = "test-api-key"
    config.api.docs_enabled = False
    app = create_app(config=config)
    app.state.lake = mock_lake
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
        headers={"X-API-Key": "test-api-key"},
    ) as ac:
        yield ac


# ---------------------------------------------------------------------------
# Record audit event
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_record_audit(client: AsyncClient, mock_lake: MagicMock) -> None:
    resp = await client.post(
        "/api/v1/audit/record",
        json={
            "event_type": "ingest",
            "dataset_name": "docs",
            "actor": "api_user",
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["success"] is True
    assert body["audit_id"] == "audit-abc-123"

    mock_lake.audit_record.assert_called_once_with(
        event_type="ingest",
        dataset_name="docs",
        actor="api_user",
        lance_version=None,
        metaflow_run_id="",
        metaflow_tags=None,
        payload=None,
    )


@pytest.mark.asyncio
async def test_record_audit_minimal(client: AsyncClient, mock_lake: MagicMock) -> None:
    resp = await client.post(
        "/api/v1/audit/record",
        json={"event_type": "system_startup"},
    )
    assert resp.status_code == 200


# ---------------------------------------------------------------------------
# Verify audit
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_verify_audit_intact(client: AsyncClient, mock_lake: MagicMock) -> None:
    resp = await client.post(
        "/api/v1/audit/verify?audit_id=audit-abc-123",
        json={},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["success"] is True
    assert body["intact"] is True

    mock_lake.audit_verify.assert_called_once_with("audit-abc-123")


# ---------------------------------------------------------------------------
# Query audit trail
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_query_audit(client: AsyncClient, mock_lake: MagicMock) -> None:
    resp = await client.get(
        "/api/v1/audit/query?dataset_name=docs&event_type=ingest",
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["success"] is True
    assert len(body["entries"]) == 2

    mock_lake.audit_query.assert_called_once_with(
        dataset_name="docs",
        start=None,
        end=None,
        event_type="ingest",
    )


@pytest.mark.asyncio
async def test_query_audit_with_date_range(client: AsyncClient, mock_lake: MagicMock) -> None:
    resp = await client.get(
        "/api/v1/audit/query?start=2026-01-01T00:00:00Z&end=2026-01-31T23:59:59Z",
    )
    assert resp.status_code == 200
    mock_lake.audit_query.assert_called_once_with(
        dataset_name=None,
        start="2026-01-01T00:00:00Z",
        end="2026-01-31T23:59:59Z",
        event_type=None,
    )


# ---------------------------------------------------------------------------
# Export audit trail
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_export_audit(client: AsyncClient, mock_lake: MagicMock) -> None:
    resp = await client.post(
        "/api/v1/audit/export?dataset_name=docs",
        json={},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["success"] is True
    assert body["export"]["dataset_name"] == "docs"
    assert body["export"]["total_entries"] == 2

    mock_lake.audit_export.assert_called_once_with("docs")


# ---------------------------------------------------------------------------
# HMAC enforcement
# ---------------------------------------------------------------------------


def test_audit_enabled_without_hmac_raises_on_startup() -> None:
    """create_app raises ValueError when audit.enabled=True but no HMAC key."""
    config = ArrowLakeConfig()
    config.api.api_key = "test-key"
    config.api.enabled = True
    config.audit.enabled = True
    config.audit.hmac_secret_key = ""

    with pytest.raises(ValueError, match="hmac_secret_key"):
        create_app(config=config)


def test_audit_enabled_with_hmac_starts_successfully() -> None:
    """create_app succeeds when audit is enabled with HMAC key configured."""
    config = ArrowLakeConfig()
    config.api.api_key = "test-key"
    config.api.enabled = True
    config.audit.enabled = True
    config.audit.hmac_secret_key = "a-very-secret-hmac-key-for-testing"

    app = create_app(config=config)
    assert app is not None


@pytest.mark.asyncio
async def test_verify_tampered_audit(mock_lake: MagicMock) -> None:
    """Verify endpoint returns intact=False for tampered entries."""
    mock_lake.audit_verify.return_value = False

    config = ArrowLakeConfig()
    config.api.api_key = "test-api-key"
    config.api.docs_enabled = False
    app = create_app(config=config)
    app.state.lake = mock_lake

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
        headers={"X-API-Key": "test-api-key"},
    ) as ac:
        resp = await ac.post(
            "/api/v1/audit/verify?audit_id=tampered-entry",
            json={},
        )
    assert resp.status_code == 200
    body = resp.json()
    assert body["success"] is True
    assert body["intact"] is False
    mock_lake.audit_verify.assert_called_once_with("tampered-entry")
