"""Data lineage request/response models."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field, field_validator


class LineageRecordRequest(BaseModel):
    operation: str = Field(..., min_length=1)
    source_datasets: list[str] | None = None
    transform_type: str = ""
    actor: str = "system"
    metadata: dict[str, Any] | None = None


class LineageRecordResponse(BaseModel):
    success: bool = True
    message: str


class LineageHistoryResponse(BaseModel):
    success: bool = True
    dataset_name: str
    events: list[dict[str, Any]]


class LineageQueryRequest(BaseModel):
    sql: str = Field(..., min_length=1, max_length=16384)

    @field_validator("sql")
    @classmethod
    def validate_sql_read_only(cls, v: str) -> str:
        from arrow_lake.api.models.common import _BLOCKED_SQL_PREFIXES

        if _BLOCKED_SQL_PREFIXES.search(v):
            raise ValueError("Only SELECT queries are allowed")
        return v


class LineageQueryResponse(BaseModel):
    success: bool = True
    data: list[dict[str, Any]]
