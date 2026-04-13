"""Arrow Lake Prometheus metrics — re-exports from core.metrics."""

from arrow_lake.core.metrics import (
    REGISTRY,
    catalog_queries_total,
    catalog_tables_total,
    disable_metrics,
    enable_metrics,
    get_metrics_enabled,
    system_uptime_seconds,
)

__all__ = [
    "REGISTRY",
    "catalog_queries_total",
    "catalog_tables_total",
    "disable_metrics",
    "enable_metrics",
    "get_metrics_enabled",
    "system_uptime_seconds",
]
