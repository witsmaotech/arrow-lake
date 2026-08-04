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
            Tune based on available RAM: each query reserves max_query_memory_mb.
            Rule of thumb: max_concurrent_queries * max_query_memory_mb <= 70% of system RAM.
            Increase for memory-heavy workloads (large joins, aggregations), decrease for
            latency-sensitive streaming queries.
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
    lance_scan_mode: str = "pyarrow_fallback"
    max_query_memory_mb: int = 512
    max_concurrent_queries: int = 4
    query_timeout_seconds: int = 300
    ducklake_enabled: bool = False
    ducklake_ttl_days: int = 7
    ducklake_max_join_rows: int = 1_000_000
    # When False (default), ``pyarrow_fallback`` stays put even for vector-less
    # datasets. The implicit promotion to native Rust lance scan in
    # OlapSearchBridge._register_dataset is the D-state IO stall that froze the
    # API on 2026-08-04. Set True only for a known-stable vector-less dataset
    # where the 13-20x LIMIT/OFFSET speedup is worth the D-state risk.
    lance_auto_promote: bool = False

    # Performance tuning
    preserve_insertion_order: bool = False
    temp_directory: str = ""
    enable_progress_bar: bool = False
    enable_profiling: bool = False
    parquet_row_group_size: int = 100_000
    ducklake_index_columns: list[str] = []

    # Connection warmup
    warmup_enabled: bool = True
    warmup_connections: int = 2

    # Query result cache
    query_cache_enabled: bool = False
    query_cache_ttl_seconds: int = 60
    query_cache_max_entries: int = 100

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
        "warmup_connections",
    )
    @classmethod
    def validate_positive_int(cls, v: int) -> int:
        if v < 1:
            raise ValueError(f"value must be >= 1, got {v}")
        return v

    def memory_budget_mb(self) -> int:
        """Total memory budget: max_concurrent_queries * max_query_memory_mb."""
        return self.max_concurrent_queries * self.max_query_memory_mb

    def validate_memory_budget(self, *, total_system_mb: int | None = None) -> str | None:
        """Validate memory budget fits within system RAM.

        Returns a warning message if the budget exceeds 70% of system RAM,
        or None if the configuration is safe.
        """
        if total_system_mb is None:
            import os

            total_system_mb = os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES") // (1024 * 1024)

        budget = self.memory_budget_mb()
        safe_limit = int(total_system_mb * 0.7)
        if budget > safe_limit:
            return (
                f"DuckDB memory budget ({budget}MB = {self.max_concurrent_queries} queries "
                f"x {self.max_query_memory_mb}MB) exceeds 70% of system RAM "
                f"({total_system_mb}MB, safe limit {safe_limit}MB). "
                f"Reduce max_concurrent_queries or max_query_memory_mb."
            )
        return None

    @field_validator("max_result_rows")
    @classmethod
    def validate_max_result_rows(cls, v: int) -> int:
        if v < 1:
            raise ValueError(f"max_result_rows must be >= 1, got {v}")
        return v
