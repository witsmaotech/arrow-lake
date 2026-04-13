"""Tests for arrow_lake.core.metrics — Story 1.5."""

from __future__ import annotations

from arrow_lake.core.metrics import (
    REGISTRY,
    catalog_queries_total,
    catalog_tables_total,
    disable_metrics,
    enable_metrics,
    get_metrics_enabled,
    system_uptime_seconds,
)


class TestMetricsRegistry:
    """Test Prometheus metrics registry."""

    def test_registry_exists(self) -> None:
        assert REGISTRY is not None

    def test_system_uptime_counter_exists(self) -> None:
        assert system_uptime_seconds is not None
        assert system_uptime_seconds._name == "arrow_lake_system_uptime_seconds"
        assert system_uptime_seconds._labelnames == ()

    def test_catalog_tables_gauge_exists(self) -> None:
        assert catalog_tables_total is not None
        assert catalog_tables_total._name == "arrow_lake_catalog_tables_total"

    def test_catalog_queries_counter_exists(self) -> None:
        assert catalog_queries_total is not None
        # Counter strips _total suffix (adds it back on export)
        assert catalog_queries_total._name == "arrow_lake_catalog_queries"

    def test_all_metrics_have_description(self) -> None:
        for metric in [system_uptime_seconds, catalog_tables_total, catalog_queries_total]:
            assert metric._documentation, f"{metric._name} missing description"

    def test_metrics_follow_naming_convention(self) -> None:
        """All metrics must have 'arrow_lake_' prefix."""
        for metric in [system_uptime_seconds, catalog_tables_total, catalog_queries_total]:
            assert metric._name.startswith("arrow_lake_"), (
                f"{metric._name} does not follow naming convention"
            )


class TestMetricsEnabled:
    """Test metrics enable/disable toggle."""

    def test_metrics_enabled_by_default(self) -> None:
        # Reset to default state
        enable_metrics()
        assert get_metrics_enabled() is True

    def test_disable_metrics(self) -> None:
        disable_metrics()
        assert get_metrics_enabled() is False
        # Clean up
        enable_metrics()

    def test_enable_metrics(self) -> None:
        disable_metrics()
        enable_metrics()
        assert get_metrics_enabled() is True


class TestMetricsCollection:
    """Test metrics can be collected."""

    def test_catalog_queries_increment(self) -> None:
        enable_metrics()
        initial = catalog_queries_total._value.get()
        catalog_queries_total.inc()
        assert catalog_queries_total._value.get() == initial + 1

    def test_catalog_tables_set(self) -> None:
        enable_metrics()
        catalog_tables_total.set(42)
        assert catalog_tables_total._value.get() == 42

    def test_system_uptime_increment(self) -> None:
        enable_metrics()
        initial = system_uptime_seconds._value.get()
        system_uptime_seconds.inc(1.0)
        assert system_uptime_seconds._value.get() == initial + 1.0


class TestMetricsReexport:
    """Test arrow_lake.metrics re-exports from core.metrics."""

    def test_registry_reexported(self) -> None:
        from arrow_lake.metrics import REGISTRY

        assert REGISTRY is REGISTRY

    def test_metrics_reexported(self) -> None:
        from arrow_lake.metrics import (
            catalog_queries_total,
            catalog_tables_total,
            system_uptime_seconds,
        )

        assert system_uptime_seconds is system_uptime_seconds
        assert catalog_tables_total is catalog_tables_total
        assert catalog_queries_total is catalog_queries_total
