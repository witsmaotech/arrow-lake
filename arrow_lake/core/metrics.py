"""Arrow Lake Prometheus metrics — Epic 1 baseline metrics.

Defines the Prometheus registry and 3 Epic 1 metrics:
- system_uptime_seconds: Gauge for system uptime tracking
- catalog_tables_total: Gauge for registered catalog table count
- catalog_queries_total: Counter for total catalog queries served

All metrics use the ``arrow_lake_`` naming prefix.
Metrics can be disabled globally via enable_metrics()/disable_metrics().
"""

from __future__ import annotations

from prometheus_client import CollectorRegistry, Counter, Gauge

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

query_latency_seconds: Gauge = Gauge(
    "arrow_lake_query_latency_seconds",
    "Query latency in seconds.",
    registry=REGISTRY,
    labelnames=["query_type"],
)

query_results_total: Counter = Counter(
    "arrow_lake_query_results_total",
    "Total number of query results returned.",
    registry=REGISTRY,
    labelnames=["query_type"],
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

# --- Metrics toggle ---

_metrics_enabled: bool = True


def get_metrics_enabled() -> bool:
    """Return whether metrics collection is enabled."""
    return _metrics_enabled


def enable_metrics() -> None:
    """Enable metrics collection."""
    global _metrics_enabled
    _metrics_enabled = True


def disable_metrics() -> None:
    """Disable metrics collection."""
    global _metrics_enabled
    _metrics_enabled = False
