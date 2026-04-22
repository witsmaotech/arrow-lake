"""OLAP analytics configuration."""

from __future__ import annotations

from pydantic import BaseModel, field_validator


class OlapConfig(BaseModel):
    """OLAP analytics configuration (Story 5.4, 7.6).

    Attributes:
        max_result_rows: Maximum number of rows returned by OLAP queries.
        enable_predicate_pushdown: Whether to push down predicates to Lance.
        enable_join: Whether JOIN queries are allowed.
        scanner_batch_size: Rows per batch when streaming via Lance scanner.
        enable_streaming: Use RecordBatchReader streaming instead of full
            materialization for SQL queries.
        lance_scan_mode: Lance scan adapter mode — "auto", "native", or
            "pyarrow_fallback".
        max_query_memory_mb: Per-query memory limit in MB.
        max_concurrent_queries: Maximum number of concurrent DuckDB queries.
        query_timeout_seconds: Per-query timeout in seconds.
        ducklake_enabled: Whether DuckLake extension is loaded for materialized
            views and cross-storage joins.
        ducklake_ttl_days: Default TTL in days for DuckLake materialized data.
        ducklake_max_join_rows: Row budget for DuckLake materialize() calls.
    """

    max_result_rows: int = 100_000
    enable_predicate_pushdown: bool = True
    enable_join: bool = True
    scanner_batch_size: int = 10_000
    enable_streaming: bool = True
    lance_scan_mode: str = "auto"
    max_query_memory_mb: int = 512
    max_concurrent_queries: int = 4
    query_timeout_seconds: int = 300
    ducklake_enabled: bool = False
    ducklake_ttl_days: int = 7
    ducklake_max_join_rows: int = 1_000_000

    @field_validator("lance_scan_mode")
    @classmethod
    def validate_lance_scan_mode(cls, v: str) -> str:
        valid = {"auto", "native", "pyarrow_fallback"}
        if v not in valid:
            raise ValueError(f"lance_scan_mode must be one of {valid}, got {v!r}")
        return v

    @field_validator(
        "max_query_memory_mb",
        "max_concurrent_queries",
        "query_timeout_seconds",
        "ducklake_ttl_days",
        "ducklake_max_join_rows",
    )
    @classmethod
    def validate_positive_int(cls, v: int) -> int:
        if v < 1:
            raise ValueError(f"value must be >= 1, got {v}")
        return v

    @field_validator("max_result_rows")
    @classmethod
    def validate_max_result_rows(cls, v: int) -> int:
        if v < 1:
            raise ValueError(f"max_result_rows must be >= 1, got {v}")
        return v
