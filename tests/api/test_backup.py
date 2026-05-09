"""API tests for backup endpoints."""

from __future__ import annotations

from dataclasses import dataclass
from unittest.mock import MagicMock, patch

import pytest
from arrow_lake.api.app import create_app
from arrow_lake.config import ArrowLakeConfig
from httpx import ASGITransport, AsyncClient


@dataclass(frozen=True)
class FakeBackupInfo:
    backup_id: str = "bk-001"
    created_at: str = "2026-05-09T00:00:00Z"
    datasets: tuple[str, ...] = ("docs",)
    blob_prefixes: tuple[str, ...] = ()
    total_size_bytes: int = 1024
    status: str = "complete"


@pytest.fixture
def mock_lake() -> MagicMock:
    lake = MagicMock()
    lake._backup_create_backup = MagicMock(return_value=FakeBackupInfo())
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


@pytest.mark.asyncio
async def test_create_backup_success(client: AsyncClient) -> None:
    with patch("arrow_lake.api.routers.backup._get_backup_mgr") as mock_mgr:
        mgr = MagicMock()
        mgr.create_backup.return_value = FakeBackupInfo()
        mock_mgr.return_value = mgr

        resp = await client.post(
            "/api/v1/backup/create",
            json={"dataset_names": ["docs"]},
        )
    assert resp.status_code == 200
    body = resp.json()
    assert body["backup_id"] == "bk-001"
    assert body["status"] == "complete"
    assert "docs" in body["datasets"]


@pytest.mark.asyncio
async def test_create_backup_with_custom_id(client: AsyncClient) -> None:
    with patch("arrow_lake.api.routers.backup._get_backup_mgr") as mock_mgr:
        mgr = MagicMock()
        mgr.create_backup.return_value = FakeBackupInfo(
            backup_id="custom-001",
        )
        mock_mgr.return_value = mgr

        resp = await client.post(
            "/api/v1/backup/create",
            json={"dataset_names": ["docs"], "backup_id": "custom-001"},
        )
    assert resp.status_code == 200
    assert resp.json()["backup_id"] == "custom-001"


@pytest.mark.asyncio
async def test_list_backups(client: AsyncClient) -> None:
    with patch("arrow_lake.api.routers.backup._get_backup_mgr") as mock_mgr:
        mgr = MagicMock()
        mgr.list_backups.return_value = [
            FakeBackupInfo(backup_id="bk-001"),
            FakeBackupInfo(backup_id="bk-002", datasets=("media",)),
        ]
        mock_mgr.return_value = mgr

        resp = await client.get("/api/v1/backup/list")
    assert resp.status_code == 200
    body = resp.json()
    assert body["count"] == 2
    assert len(body["backups"]) == 2


@pytest.mark.asyncio
async def test_delete_backup(client: AsyncClient) -> None:
    with patch("arrow_lake.api.routers.backup._get_backup_mgr") as mock_mgr:
        mgr = MagicMock()
        mgr.delete_backup.return_value = None
        mock_mgr.return_value = mgr

        resp = await client.delete("/api/v1/backup/bk-001")
    assert resp.status_code == 200
    assert "deleted" in resp.json()["message"].lower()


@pytest.mark.asyncio
async def test_restore_backup(client: AsyncClient) -> None:
    with patch("arrow_lake.api.routers.backup._get_backup_mgr") as mock_mgr:
        mgr = MagicMock()
        mgr.restore_backup.return_value = FakeBackupInfo(status="restored")
        mock_mgr.return_value = mgr

        resp = await client.post(
            "/api/v1/backup/restore?backup_id=bk-001",
            json={},
        )
    assert resp.status_code == 200
    assert resp.json()["status"] == "restored"


@pytest.mark.asyncio
async def test_create_backup_no_auth() -> None:
    config = ArrowLakeConfig()
    config.api.api_key = "test-api-key"
    config.api.docs_enabled = False
    app = create_app(config=config)
    app.state.lake = MagicMock()

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
    ) as ac:
        resp = await ac.post(
            "/api/v1/backup/create",
            json={"dataset_names": ["docs"]},
        )
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_list_backups_storage_error(client: AsyncClient) -> None:
    from arrow_lake.exceptions import ErrorCode, StorageError

    with patch("arrow_lake.api.routers.backup._get_backup_mgr") as mock_mgr:
        mgr = MagicMock()
        mgr.list_backups.side_effect = StorageError(
            error_code=ErrorCode.BLOB_NOT_FOUND,
            message="No backups found",
        )
        mock_mgr.return_value = mgr

        resp = await client.get("/api/v1/backup/list")
    assert resp.status_code == 404
