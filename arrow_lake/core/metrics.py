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
