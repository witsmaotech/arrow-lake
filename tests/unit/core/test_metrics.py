"""Unit tests for arrow_lake.core.metrics — enable/disable/toggle, QueryTimer."""

from __future__ import annotations

import threading
import time

from arrow_lake.core.metrics import (
    REGISTRY,
    catalog_queries_total,
    catalog_tables_total,
    disable_metrics,
    enable_metrics,
    get_metrics_enabled,
    query_latency_seconds,
    query_total,
    rate_limit_rejected_total,
    system_uptime_seconds,
)


class TestMetricsToggle:
    """Test metrics enable/disable toggle."""

    def test_enabled_by_default(self):
        assert get_metrics_enabled() is True

    def test_disable_and_enable(self):
        disable_metrics()
        try:
            assert get_metrics_enabled() is False
        finally:
            enable_metrics()

        assert get_metrics_enabled() is True

    def test_toggle_is_thread_safe(self):
        """Toggle from multiple threads should not corrupt state."""
        errors: list[str] = []

        def toggle_n(n: int):
            try:
                for _ in range(n):
                    disable_metrics()
                    enable_metrics()
            except Exception as exc:
                errors.append(str(exc))

        threads = [threading.Thread(target=toggle_n, args=(100,)) for _ in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert errors == []
        assert get_metrics_enabled() is True


class TestMetricsRegistration:
    """Test that metrics are properly registered with correct names and labels."""

    def test_system_uptime_registered(self):
        assert system_uptime_seconds._name == "arrow_lake_system_uptime_seconds"

    def test_catalog_metrics_registered(self):
        assert catalog_tables_total._name == "arrow_lake_catalog_tables_total"
        assert catalog_queries_total._name == "arrow_lake_catalog_queries"

    def test_query_metrics_registered(self):
        assert query_total._name == "arrow_lake_query"
        assert "query_type" in query_total._labelnames

    def test_query_latency_histogram(self):
        assert query_latency_seconds._name == "arrow_lake_query_latency_seconds"
        assert hasattr(query_latency_seconds, "DEFAULT_BUCKETS")

    def test_rate_limit_registered(self):
        assert rate_limit_rejected_total._name == "arrow_lake_rate_limit_rejected"

    def test_registry_collects_metrics(self):
        """REGISTRY should collect all registered metrics."""
        output = list(REGISTRY.collect())
        names = {family.name for family in output}
        assert "arrow_lake_system_uptime_seconds" in names
        assert "arrow_lake_query" in names


class TestQueryTimer:
    """Test the _QueryTimer context manager."""

    @staticmethod
    def _get_counter_value() -> float:
        """Read current query_total counter value."""
        for family in query_total.collect():
            for sample in family.samples:
                if sample.name == "arrow_lake_query_total":
                    return sample.value
        return 0.0

    def test_timer_records_query(self):
        from arrow_lake.core.metrics import _QueryTimer

        before = self._get_counter_value()

        with _QueryTimer(query_type="test"):
            time.sleep(0.01)

        after = self._get_counter_value()
        assert after == before + 1

    def test_timer_respects_disabled_metrics(self):
        from arrow_lake.core.metrics import _QueryTimer

        disable_metrics()
        try:
            before = self._get_counter_value()

            with _QueryTimer(query_type="test_disabled"):
                pass

            after = self._get_counter_value()
            assert after == before
        finally:
            enable_metrics()
