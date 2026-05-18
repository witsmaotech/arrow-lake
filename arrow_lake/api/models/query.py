"""Query request/response models (Sprint 4)."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator, model_validator

# ---------------------------------------------------------------------------
# OLAP / metadata query
# ---------------------------------------------------------------------------

class OlapQueryRequest(BaseModel):
    sql: str = Field(..., min_length=1, max_length=16384)
    max_rows: int | None = Field(default=None, ge=1, le=1000000)
    format: Literal["arrow_ipc", "json"] = "json"

    @field_validator("sql")
    @classmethod
    def validate_sql_read_only(cls, v: str) -> str:
        from arrow_lake.api.models.common import _BLOCKED_SQL_PREFIXES

        if _BLOCKED_SQL_PREFIXES.search(v):
            raise ValueError("Only SELECT queries are allowed")
        return v


class OlapQueryResponse(BaseModel):
    success: bool = True
    format: str
    row_count: int
    column_count: int
    meta: dict[str, Any] | None = None
    data: str | None = None
    rows: list[dict[str, Any]] | None = None


# ---------------------------------------------------------------------------
# Daft query
# ---------------------------------------------------------------------------

class DaftSortStep(BaseModel):
    """Sort by a column."""
    column: str
    desc: bool = False


class DaftFilterStep(BaseModel):
    """Filter rows by column conditions."""
    column: str
    op: Literal["eq", "ne", "gt", "gte", "lt", "lte", "is_null", "is_not_null"]
    value: Any = None


class DaftGroupByStep(BaseModel):
    """Group by columns then aggregate."""
    columns: list[str] = Field(..., min_length=1)
    agg: Literal["sum", "mean", "count", "min", "max", "stddev", "var"] = "sum"


class DaftJoinStep(BaseModel):
    """Join with another dataset."""
    dataset: str
    on: str
    how: Literal["inner", "left", "outer"] = "inner"


class DaftSqlStep(BaseModel):
    """Execute SQL query against current frame (bound as ``self``)."""
    query: str = Field(..., min_length=1, max_length=10000)


class DaftPivotStep(BaseModel):
    """Pivot column into wide format."""
    group_by: str
    pivot_col: str
    value_col: str
    agg_fn: Literal["sum", "mean", "count", "min", "max", "first", "last"] = "sum"


class DaftExplodeStep(BaseModel):
    """Explode list column(s) into rows."""
    columns: list[str] = Field(..., min_length=1)


class DaftSampleStep(BaseModel):
    """Random sample of rows."""
    fraction: float | None = Field(default=None, gt=0.0, le=1.0)
    size: int | None = Field(default=None, ge=1)
    seed: int | None = None


class DaftQueryRequest(BaseModel):
    """Daft DataFrame query with chained operations.

    Operations execute in order: sort → filter → groupby → join → sql →
    pivot → explode → sample → distinct → select → offset → limit → collect.
    """
    columns: list[str] | None = None
    sort: DaftSortStep | None = None
    filters: list[DaftFilterStep] | None = None
    groupby: DaftGroupByStep | None = None
    join: DaftJoinStep | None = None
    sql: DaftSqlStep | None = None
    pivot: DaftPivotStep | None = None
    explode: DaftExplodeStep | None = None
    sample: DaftSampleStep | None = None
    distinct: bool = False
    offset: int | None = Field(default=None, ge=0)
    limit: int = Field(default=10_000, ge=1)
    max_rows: int | None = Field(default=None, ge=0)
    format: Literal["arrow_ipc", "json"] = "json"

    @model_validator(mode="after")
    def validate_sql_keywords(self) -> DaftQueryRequest:
        if self.sql is not None:
            from arrow_lake.api.models.common import _BLOCKED_SQL_PREFIXES
            if _BLOCKED_SQL_PREFIXES.search(self.sql.query):
                raise ValueError("SQL query contains forbidden write/DDL keywords")
        return self


class DaftQueryResponse(BaseModel):
    success: bool = True
    format: str
    row_count: int
    column_count: int
    data: str | None = None
    rows: list[dict[str, Any]] | None = None


# ---------------------------------------------------------------------------
# Export
# ---------------------------------------------------------------------------

class ExportRequest(BaseModel):
    output_path: str = Field(..., min_length=1)
    format: str | None = None
    columns: list[str] | None = None
    version: int | None = None
    compression: str | None = None
    overwrite: bool = False

    @field_validator("output_path")
    @classmethod
    def validate_no_traversal(cls, v: str) -> str:
        if ".." in v:
            raise ValueError(f"Path traversal not allowed: {v!r}")
        import os
        if os.path.isabs(v):
            raise ValueError(f"Absolute paths not allowed: {v!r}")
        if "\0" in v:
            raise ValueError(f"Null byte not allowed in path: {v!r}")
        return v


class ExportResponse(BaseModel):
    success: bool = True
    dataset_name: str
    output_path: str
    format: str
    row_count: int
    column_count: int
    file_size_bytes: int
    version: int | None = None


# ---------------------------------------------------------------------------
# Async export task tracking
# ---------------------------------------------------------------------------


class ExportTaskResponse(BaseModel):
    success: bool = True
    task_id: str
    dataset_name: str
    status: str
    message: str = ""


class ExportTaskStatusResponse(BaseModel):
    success: bool = True
    task_id: str
    status: str
    progress: float = 0.0
    created_at: str = ""
    completed_at: str | None = None
    error: str | None = None
    result: dict[str, Any] | None = None
