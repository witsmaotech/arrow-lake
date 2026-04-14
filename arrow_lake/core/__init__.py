"""Arrow Lake core module."""

from arrow_lake.core.logging import configure_logging, get_logger
from arrow_lake.core.metrics import (
    REGISTRY,
    catalog_queries_total,
    catalog_tables_total,
    disable_metrics,
    enable_metrics,
    get_metrics_enabled,
    ingestion_bytes_total,
    ingestion_duration_seconds,
    ingestion_errors_total,
    ingestion_rows_total,
    processing_active_tasks,
    processing_embeddings_total,
    processing_quality_rejects_total,
    query_latency_seconds,
    query_results_total,
    query_total,
    system_uptime_seconds,
)

__all__ = [
    "REGISTRY",
    "catalog_queries_total",
    "catalog_tables_total",
    "configure_logging",
    "disable_metrics",
    "enable_metrics",
    "get_logger",
    "get_metrics_enabled",
    "ingestion_bytes_total",
    "ingestion_duration_seconds",
    "ingestion_errors_total",
    "ingestion_rows_total",
    "processing_active_tasks",
    "processing_embeddings_total",
    "processing_quality_rejects_total",
    "query_latency_seconds",
    "query_results_total",
    "query_total",
    "system_uptime_seconds",
]
