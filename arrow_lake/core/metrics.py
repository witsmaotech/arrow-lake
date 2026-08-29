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

import threading
from typing import Any

from prometheus_client import CollectorRegistry, Counter, Gauge, Histogram

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

ingest_executor_active_threads: Gauge = Gauge(
    "arrow_lake_ingest_executor_active_threads",
    "In-flight submissions (running + queued) on the ingest/background pool.",
    registry=REGISTRY,
)

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

duckdb_pool_health_checks_total: Counter = Counter(
    "arrow_lake_duckdb_pool_health_checks_total",
    "Total number of DuckDB connection health checks performed.",
    registry=REGISTRY,
)

duckdb_pool_evicted_connections_total: Counter = Counter(
    "arrow_lake_duckdb_pool_evicted_connections_total",
    "Total number of DuckDB connections evicted (idle timeout or zombie).",
    registry=REGISTRY,
)

duckdb_pool_warmup_total: Counter = Counter(
    "arrow_lake_duckdb_pool_warmup_total",
    "Total number of DuckDB connections warmed up at startup.",
    registry=REGISTRY,
)

duckdb_pool_warmup_errors_total: Counter = Counter(
    "arrow_lake_duckdb_pool_warmup_errors_total",
    "Total number of DuckDB warmup connection failures.",
    registry=REGISTRY,
)

# ---------------------------------------------------------------------------
# Circuit Breaker Metrics (v1.6.0 Phase 2)
# ---------------------------------------------------------------------------

circuit_breaker_state: Gauge = Gauge(
    "arrow_lake_circuit_breaker_state",
    "Current circuit breaker state (0=closed, 1=half_open, 2=open).",
    registry=REGISTRY,
    labelnames=["name"],
)

circuit_breaker_failures: Counter = Counter(
    "arrow_lake_circuit_breaker_failures_total",
    "Total circuit breaker failures recorded.",
    registry=REGISTRY,
    labelnames=["name"],
)

circuit_breaker_successes: Counter = Counter(
    "arrow_lake_circuit_breaker_successes_total",
    "Total circuit breaker successes recorded.",
    registry=REGISTRY,
    labelnames=["name"],
)

circuit_breaker_state_transitions: Counter = Counter(
    "arrow_lake_circuit_breaker_state_transitions_total",
    "Total circuit breaker state transitions.",
    registry=REGISTRY,
    labelnames=["name", "from_state", "to_state"],
)

duckdb_memory_budget_mb: Gauge = Gauge(
    "arrow_lake_duckdb_memory_budget_mb",
    "Configured DuckDB memory budget in MB (max_concurrent_queries * max_query_memory_mb).",
    registry=REGISTRY,
)

# --- v1.4.3: Maintenance Metrics ---

maintenance_compaction_runs_total: Counter = Counter(
    "arrow_lake_maintenance_compaction_runs_total",
    "Total number of dataset compaction runs.",
    registry=REGISTRY,
    labelnames=["dataset"],
)

maintenance_vacuum_runs_total: Counter = Counter(
    "arrow_lake_maintenance_vacuum_runs_total",
    "Total number of version cleanup runs.",
    registry=REGISTRY,
    labelnames=["dataset"],
)

maintenance_compaction_fragments_delta: Gauge = Gauge(
    "arrow_lake_maintenance_compaction_fragments_delta",
    "Change in fragment count from last compaction (before - after).",
    registry=REGISTRY,
    labelnames=["dataset"],
)

maintenance_cycle_duration_seconds: Gauge = Gauge(
    "arrow_lake_maintenance_cycle_duration_seconds",
    "Duration of the last maintenance cycle in seconds.",
    registry=REGISTRY,
)

maintenance_last_run_timestamp: Gauge = Gauge(
    "arrow_lake_maintenance_last_run_timestamp",
    "Unix timestamp of the last maintenance cycle completion.",
    registry=REGISTRY,
)

# --- v1.4.3: Quality Gate Metrics ---

quality_check_total: Counter = Counter(
    "arrow_lake_quality_check_total",
    "Total number of quality gate checks run.",
    registry=REGISTRY,
    labelnames=["dataset"],
)

quality_reject_total: Counter = Counter(
    "arrow_lake_quality_reject_total",
    "Total number of rows rejected by quality gates.",
    registry=REGISTRY,
    labelnames=["dataset", "reason"],
)

# --- v1.11.0.1 W3.1: Dataset Contract Gate Metrics (DR13/DR14) ---

contract_check_total: Counter = Counter(
    "arrow_lake_contract_check_total",
    "Total number of contract gate checks run (result: pass|reject).",
    registry=REGISTRY,
    labelnames=["dataset", "result"],
)

quality_score_distribution: Histogram = Histogram(
    "arrow_lake_quality_score_distribution",
    "Distribution of quality scores for ingested rows.",
    registry=REGISTRY,
    labelnames=["dataset"],
    buckets=[0.0, 0.2, 0.4, 0.6, 0.8, 1.0],
)

# --- v1.11.0 MS1: Ontology (SHACL) Gate Metrics ---

ontology_check_total: Counter = Counter(
    "arrow_lake_ontology_check_total",
    "Total number of ontology gate validations run at KG build finish.",
    registry=REGISTRY,
    labelnames=["dataset", "result"],
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


# v1.11.2 backlog(M-12/M-13/M-16):quality gate 可观测三件
quality_gate_wiring_failures_total = Counter(
    "arrow_lake_quality_gate_wiring_failures_total",
    "Quality gate construction failures (gate=None, ingest un-gated)",
)
quality_dead_letter_failures_total = Counter(
    "arrow_lake_quality_dead_letter_failures_total",
    "Dead-letter write failures (rows lost from DLQ, not just rejected)",
)
quality_gate_truncated_total = Counter(
    "arrow_lake_quality_gate_truncated_total",
    "Quality gate stage truncation (M-16 schema row-cap sampling)",
    ["stage"],
)
