"""OLAP analytics configuration."""

from __future__ import annotations

from pydantic import BaseModel, field_validator


class OlapConfig(BaseModel):
    """OLAP analytics configuration (Story 5.4, 7.6).

    Attributes:
        max_result_rows: Maximum number of rows returned by OLAP queries.
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
        lance_scan_mode_overrides: Per-dataset lance scan mode (v1.10.4). Maps a
            dataset name to one of the scan modes; opt-in datasets only. A vector
            dataset listed here is demoted back to the global mode with a warning
            (native lance scan panics on IVF_PQ vector streams).
        lance_breaker_trip_threshold: D-state (uninterruptible) events within the
            window that trip the breaker for a dataset.
        lance_breaker_window_seconds: Sliding window for counting trips.
        lance_breaker_cooldown_seconds: How long a tripped dataset stays demoted
            to pyarrow_fallback before retrying native.
    """

    max_result_rows: int = 100_000
    enable_join: bool = True
    scanner_batch_size: int = 10_000
    enable_streaming: bool = True
    lance_scan_mode: str = "pyarrow_fallback"
    max_query_memory_mb: int = 512
    max_concurrent_queries: int = 4
    # MUST stay below the gunicorn `--timeout` (120s in dev). run_duckdb_interruptible
    # aborts a query at this threshold via conn.interrupt(); if it exceeds gunicorn's
    # worker timeout, gunicorn SIGABRTs the whole worker first (native
    # `terminate called` → in-flight requests dropped → "Failed to fetch"). 90s gives
    # the watchdog a clean 30s window to interrupt before gunicorn reaps the worker.
    query_timeout_seconds: int = 90
    ducklake_enabled: bool = False
    ducklake_ttl_days: int = 7
    ducklake_max_join_rows: int = 1_000_000
    # Default False (was True pre-2026-08-07): vector-less datasets stay on the
    # PyArrow fallback path. The native Rust lance scanner (13-20x faster LIMIT/
    # OFFSET) intermittently enters D-state (uninterruptible) IO — conn.interrupt()
    # cannot break D-state, so the watchdog daemonizes the worker thread; once the
    # session is recycled that thread races the next user on the same connection →
    # native `std::terminate` → worker SIGABRT → whole OLAP path wedges
    # (2026-08-07 outage). Force pyarrow_fallback everywhere for stability; re-enable
    # per-dataset only once Lance/DuckDB D-state is fixed upstream.
    lance_auto_promote: bool = False
    # v1.10.4: per-dataset opt-in to native lance scan (e.g. large vector-less
    # analytical datasets like `ontime` 107M rows: native is 34-145x faster). Only
    # listed datasets are affected; vector datasets are hard-demoted by the resolver.
    lance_scan_mode_overrides: dict[str, str] = {}
    # Breaker thresholds (env-overridable). Default conservative (2 trips / 10min →
    # 30min cooldown): a single D-state wedges the whole OLAP path, so prefer strict.
    lance_breaker_trip_threshold: int = 2
    lance_breaker_window_seconds: int = 600
    lance_breaker_cooldown_seconds: int = 1800

    # Performance tuning
    preserve_insertion_order: bool = False
    temp_directory: str = ""
    enable_progress_bar: bool = False
    enable_profiling: bool = False
    parquet_row_group_size: int = 100_000
    ducklake_index_columns: list[str] = []
    # P0-1 (C1, 2026-08-21): DuckDB virtual filesystems disabled on every OLAP
    # session. `read_text('/proc/self/environ')` class table functions would
    # otherwise exfiltrate all container secrets through user SQL. Default blocks
    # local files; extend (e.g. ["LocalFileSystem", "S3"]) to also stop direct
    # object-storage scans that bypass dataset ACLs. User SQL is additionally
    # guarded by the table-function blacklist in validation.py.
    disabled_filesystems: list[str] = ["LocalFileSystem"]

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

    @field_validator("lance_scan_mode_overrides")
    @classmethod
    def validate_lance_scan_mode_overrides(cls, v: dict[str, str]) -> dict[str, str]:
        valid = {"auto", "native", "pyarrow_fallback"}
        bad = {ds: mode for ds, mode in v.items() if mode not in valid}
        if bad:
            raise ValueError(
                f"lance_scan_mode_overrides values must be one of {valid}, got {bad}"
            )
        return v

    @field_validator(
        "max_query_memory_mb",
        "max_concurrent_queries",
        "query_timeout_seconds",
        "ducklake_ttl_days",
        "ducklake_max_join_rows",
        "warmup_connections",
        "lance_breaker_trip_threshold",
        "lance_breaker_window_seconds",
        "lance_breaker_cooldown_seconds",
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
