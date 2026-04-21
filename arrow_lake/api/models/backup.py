"""Backup API request/response models."""

from __future__ import annotations

from pydantic import BaseModel, Field


class CreateBackupRequest(BaseModel):
    """Request body for creating a backup."""

    dataset_names: list[str] | None = Field(default=None, description="Datasets to backup")
    blob_prefixes: list[str] | None = Field(default=None, description="S3 prefixes to backup")
    backup_id: str | None = Field(default=None, description="Custom backup ID")


class RestoreBackupRequest(BaseModel):
    """Request body for restoring a backup."""

    dataset_names: list[str] | None = Field(default=None, description="Datasets to restore")
    blob_prefixes: list[str] | None = Field(default=None, description="Blob prefixes to restore")
    overwrite: bool = Field(default=False, description="Overwrite existing datasets")


class BackupInfoResponse(BaseModel):
    """Response model for backup metadata."""

    backup_id: str
    created_at: str
    datasets: list[str]
    blob_prefixes: list[str]
    total_size_bytes: int
    status: str


class BackupListResponse(BaseModel):
    """Response model for listing backups."""

    backups: list[BackupInfoResponse]
    count: int
