"""Arrow Lake core module."""

from arrow_lake.core.logging import configure_logging, get_logger
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
    "configure_logging",
    "disable_metrics",
    "enable_metrics",
    "get_logger",
    "get_metrics_enabled",
    "system_uptime_seconds",
]
