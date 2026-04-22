"""Arrow Lake Prometheus metrics — baseline metrics.

Defines the Prometheus registry and metrics:
- system_uptime_seconds: Gauge for system uptime tracking
- catalog_tables_total: Gauge for registered catalog table count
- catalog_queries_total: Counter for total catalog queries served
- query_latency_seconds: Histogram for query latency (p50/p95/p99)
- http_request_duration_seconds: Histogram for HTTP request latency
- auth_requests_total: Counter for authentication attempts
- rate_limit_rejected_total: Counter for rate limit rejections

All metrics use the ``arrow_lake_`` naming prefix.
Metrics can be disabled globally via enable_metrics()/disable_metrics().
"""

from __future__ import annotations

from prometheus_client import CollectorRegistry, Counter, Gauge, Histogram
import threading
from typing import Any

REGISTRY = CollectorRegistry()

# --- Epic 1 Metrics ---

system_uptime_seconds: Gauge = Gauge(
    "arrow_lake_system_uptime_seconds",
    "Seconds since Arrow Lake system started.",
    registry=REGISTRY,
)

catalog_tables_total: Gauge = Gauge(
    "arrow_lake_catalog_tables_total",
    "Number of tables registered in the Catalog.",
    registry=REGISTRY,
)

catalog_queries_total: Counter = Counter(
    "arrow_lake_catalog_queries_total",
    "Total number of catalog queries served.",
    registry=REGISTRY,
)

# --- Story 7.8: Ingestion Metrics (FR-OBS-02) ---

ingestion_rows_total: Counter = Counter(
    "arrow_lake_ingestion_rows_total",
    "Total number of rows ingested.",
    registry=REGISTRY,
    labelnames=["source"],
)

ingestion_bytes_total: Counter = Counter(
    "arrow_lake_ingestion_bytes_total",
    "Total bytes ingested.",
    registry=REGISTRY,
    labelnames=["source"],
)

ingestion_duration_seconds: Gauge = Gauge(
    "arrow_lake_ingestion_duration_seconds",
    "Duration of ingestion operation in seconds.",
    registry=REGISTRY,
    labelnames=["source"],
)

ingestion_errors_total: Counter = Counter(
    "arrow_lake_ingestion_errors_total",
    "Total number of ingestion errors.",
    registry=REGISTRY,
    labelnames=["source", "error_type"],
)

# --- Story 7.8: Processing Metrics (FR-OBS-03) ---

processing_embeddings_total: Counter = Counter(
    "arrow_lake_processing_embeddings_total",
    "Total number of embeddings generated.",
    registry=REGISTRY,
    labelnames=["model"],
)

processing_quality_rejects_total: Counter = Counter(
    "arrow_lake_processing_quality_rejects_total",
    "Total number of rows rejected by quality filters.",
    registry=REGISTRY,
    labelnames=["filter_name"],
)

processing_active_tasks: Gauge = Gauge(
    "arrow_lake_processing_active_tasks",
    "Number of currently active processing tasks.",
    registry=REGISTRY,
)

# --- Story 7.8: Query Metrics (FR-OBS-04) ---

query_total: Counter = Counter(
    "arrow_lake_query_total",
    "Total number of queries executed.",
    registry=REGISTRY,
    labelnames=["query_type"],
)

query_latency_seconds: Histogram = Histogram(
    "arrow_lake_query_latency_seconds",
    "Query latency in seconds.",
    registry=REGISTRY,
    labelnames=["query_type"],
    buckets=[0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0],
)

query_results_total: Counter = Counter(
    "arrow_lake_query_results_total",
    "Total number of query results returned.",
    registry=REGISTRY,
    labelnames=["query_type"],
)

# --- M4: HTTP and Auth metrics ---

http_request_duration_seconds: Histogram = Histogram(
    "arrow_lake_http_request_duration_seconds",
    "HTTP request duration in seconds.",
    registry=REGISTRY,
    labelnames=["method", "path", "status_code"],
    buckets=[0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0],
)

auth_requests_total: Counter = Counter(
    "arrow_lake_auth_requests_total",
    "Total number of authentication attempts.",
    registry=REGISTRY,
    labelnames=["auth_method", "status"],
)

# --- M5: Rate Limit Metrics ---

rate_limit_rejected_total: Counter = Counter(
    "arrow_lake_rate_limit_rejected_total",
    "Total number of requests rejected by rate limiting.",
    registry=REGISTRY,
    labelnames=["endpoint", "path"],
)

# --- Epic 6: Workflow Metrics ---

workflow_steps_total: Counter = Counter(
    "arrow_lake_workflow_steps_total",
    "Total number of workflow step executions.",
    registry=REGISTRY,
    labelnames=["flow_name", "step_name", "status"],
)

workflow_step_duration_seconds: Gauge = Gauge(
    "arrow_lake_workflow_step_duration_seconds",
    "Duration of workflow step execution in seconds.",
    registry=REGISTRY,
    labelnames=["flow_name", "step_name"],
)

workflow_retries_total: Counter = Counter(
    "arrow_lake_workflow_retries_total",
    "Total number of workflow step retries.",
    registry=REGISTRY,
    labelnames=["flow_name", "step_name"],
)

# --- Phase 3: DuckDB Session Pool Metrics ---

duckdb_pool_active_sessions: Gauge = Gauge(
    "arrow_lake_duckdb_pool_active_sessions",
    "Number of currently active DuckDB sessions.",
    registry=REGISTRY,
)

duckdb_pool_queued_requests: Gauge = Gauge(
    "arrow_lake_duckdb_pool_queued_requests",
    "Number of requests waiting for a DuckDB session.",
    registry=REGISTRY,
)

duckdb_pool_total_queries: Counter = Counter(
    "arrow_lake_duckdb_pool_total_queries",
    "Total number of DuckDB queries executed.",
    registry=REGISTRY,
)

duckdb_pool_total_errors: Counter = Counter(
    "arrow_lake_duckdb_pool_total_errors",
    "Total number of DuckDB query errors.",
    registry=REGISTRY,
)

duckdb_pool_total_timeouts: Counter = Counter(
    "arrow_lake_duckdb_pool_total_timeouts",
    "Total number of DuckDB session acquisition timeouts.",
    registry=REGISTRY,
)

duckdb_pool_slow_queries: Counter = Counter(
    "arrow_lake_duckdb_pool_slow_queries",
    "Total number of slow DuckDB queries exceeding threshold.",
    registry=REGISTRY,
)

# --- Metrics toggle (thread-safe via Event) ---

_metrics_enabled = threading.Event()
_metrics_enabled.set()


def get_metrics_enabled() -> bool:
    """Return whether metrics collection is enabled."""
    return _metrics_enabled.is_set()


def enable_metrics() -> None:
    """Enable metrics collection."""
    _metrics_enabled.set()


def disable_metrics() -> None:
    """Disable metrics collection."""
    _metrics_enabled.clear()


class _QueryTimer:
    """Context manager that records query count and latency."""

    def __init__(self, query_type: str) -> None:
        self._query_type = query_type
        self._timer: Any = None

    def __enter__(self) -> _QueryTimer:
        if _metrics_enabled:
            query_total.labels(query_type=self._query_type).inc()
            self._timer = query_latency_seconds.labels(query_type=self._query_type).time()
            self._timer.__enter__()
        return self

    def __exit__(self, *args: Any) -> None:
        if self._timer is not None:
            self._timer.__exit__(*args)
