"""Backup endpoints: create, restore, list, delete."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from arrow_lake.api.auth_models import Role
from arrow_lake.api.deps import get_config, require_role
from arrow_lake.api.models.backup import (
    BackupInfoResponse,
    BackupListResponse,
    CreateBackupRequest,
    RestoreBackupRequest,
)
from arrow_lake.api.models.common import MessageResponse
from arrow_lake.config import ArrowLakeConfig
from arrow_lake.exceptions import ErrorCode, StorageError
from arrow_lake.ops.backup import BackupManager
from arrow_lake.storage.blob_store import BlobStoreManager

router = APIRouter(prefix="/api/v1/backup", tags=["backup"])


def _get_backup_mgr(config: ArrowLakeConfig) -> BackupManager:
    """Create a BackupManager from config (shared blob_store per request)."""
    blob_store = BlobStoreManager(config.storage)
    return BackupManager(
        storage_config=config.storage,
        lance_base_uri=config.storage.base_uri,
        blob_store=blob_store,
    )


@router.post("/create", response_model=BackupInfoResponse)
async def create_backup(
    req: CreateBackupRequest,
    config: ArrowLakeConfig = Depends(get_config),
    _user: dict = Depends(require_role(Role.ADMIN)),
) -> BackupInfoResponse:
    """Create a backup of Lance datasets and/or blob prefixes."""
    mgr = _get_backup_mgr(config)

    try:
        info = mgr.create_backup(
            dataset_names=req.dataset_names,
            blob_prefixes=req.blob_prefixes,
            backup_id=req.backup_id,
        )
    except StorageError as exc:
        raise HTTPException(
            status_code=_error_to_status(exc.error_code),
            detail=exc.message,
        ) from exc

    return BackupInfoResponse(
        backup_id=info.backup_id,
        created_at=info.created_at,
        datasets=list(info.datasets),
        blob_prefixes=list(info.blob_prefixes),
        total_size_bytes=info.total_size_bytes,
        status=info.status,
    )


@router.post("/restore", response_model=BackupInfoResponse)
async def restore_backup(
    backup_id: str,
    req: RestoreBackupRequest,
    config: ArrowLakeConfig = Depends(get_config),
    _user: dict = Depends(require_role(Role.ADMIN)),
) -> BackupInfoResponse:
    """Restore a backup by ID."""
    mgr = _get_backup_mgr(config)

    try:
        info = mgr.restore_backup(
            backup_id=backup_id,
            dataset_names=req.dataset_names,
            blob_prefixes=req.blob_prefixes,
            overwrite=req.overwrite,
        )
    except StorageError as exc:
        raise HTTPException(
            status_code=_error_to_status(exc.error_code),
            detail=exc.message,
        ) from exc

    return BackupInfoResponse(
        backup_id=info.backup_id,
        created_at=info.created_at,
        datasets=list(info.datasets),
        blob_prefixes=list(info.blob_prefixes),
        total_size_bytes=info.total_size_bytes,
        status=info.status,
    )


@router.get("/list", response_model=BackupListResponse)
async def list_backups(
    config: ArrowLakeConfig = Depends(get_config),
) -> BackupListResponse:
    """List all available backups."""
    mgr = _get_backup_mgr(config)

    try:
        backups = mgr.list_backups()
    except StorageError as exc:
        raise HTTPException(
            status_code=_error_to_status(exc.error_code),
            detail=exc.message,
        ) from exc

    return BackupListResponse(
        backups=[
            BackupInfoResponse(
                backup_id=b.backup_id,
                created_at=b.created_at,
                datasets=list(b.datasets),
                blob_prefixes=list(b.blob_prefixes),
                total_size_bytes=b.total_size_bytes,
                status=b.status,
            )
            for b in backups
        ],
        count=len(backups),
    )


@router.delete("/{backup_id}", response_model=MessageResponse)
async def delete_backup(
    backup_id: str,
    config: ArrowLakeConfig = Depends(get_config),
    _user: dict = Depends(require_role(Role.ADMIN)),
) -> MessageResponse:
    """Delete a backup and all its data."""
    mgr = _get_backup_mgr(config)

    try:
        mgr.delete_backup(backup_id)
    except StorageError as exc:
        raise HTTPException(
            status_code=_error_to_status(exc.error_code),
            detail=exc.message,
        ) from exc

    return MessageResponse(message=f"Backup '{backup_id}' deleted")


def _error_to_status(code: ErrorCode) -> int:
    """Map ErrorCode to HTTP status code."""
    mapping = {
        ErrorCode.BLOB_NOT_FOUND: 404,
        ErrorCode.STORAGE_READ_FAILED: 500,
        ErrorCode.STORAGE_WRITE_FAILED: 409,
        ErrorCode.BLOB_UPLOAD_FAILED: 500,
        ErrorCode.BLOB_DELETE_FAILED: 500,
        ErrorCode.BLOB_DOWNLOAD_FAILED: 500,
    }
    return mapping.get(code, 500)
