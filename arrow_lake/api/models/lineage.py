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

        stripped = v.strip()
        if not stripped.upper().startswith("SELECT"):
            raise ValueError("Only SELECT queries are allowed")
        if ";" in stripped.rstrip(";"):
            raise ValueError("Multi-statement queries are not allowed")
        if _BLOCKED_SQL_PREFIXES.search(v):
            raise ValueError("Only SELECT queries are allowed")
        return v


class LineageQueryResponse(BaseModel):
    success: bool = True
    data: list[dict[str, Any]]


class LineageNode(BaseModel):
    id: str
    depth: int = 0
    type: str = "source"


class LineageEdge(BaseModel):
    from_: str = Field(alias="from")
    to: str
    operation: str = ""
    transform_type: str = ""

    model_config = {"populate_by_name": True}


class LineageGraphStats(BaseModel):
    total_nodes: int = 0
    total_edges: int = 0
    max_depth: int = 0


class LineageGraphResponse(BaseModel):
    success: bool = True
    dataset_name: str
    nodes: list[LineageNode] = []
    edges: list[LineageEdge] = []
    stats: LineageGraphStats = LineageGraphStats()


class LineageImpactRequest(BaseModel):
    dataset_name: str = Field(..., min_length=1)


class LineageImpactItem(BaseModel):
    dataset: str
    depth: int
    operation: str = ""
    transform_type: str = ""


class LineageImpactResponse(BaseModel):
    success: bool = True
    source_dataset: str
    impacted_datasets: list[LineageImpactItem] = []


class LineageStatsResponse(BaseModel):
    success: bool = True
    total_datasets_tracked: int = 0
    total_events: int = 0
