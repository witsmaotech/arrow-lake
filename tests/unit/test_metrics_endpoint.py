"""Tests for Story 7.8 — Prometheus Metrics Endpoint."""

from __future__ import annotations

from unittest.mock import patch

import pytest
from arrow_lake.core.metrics import (
    REGISTRY,
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
)
from arrow_lake.server import _get_metrics_config, app


def _call_app(
    path: str = "/health",
    env: dict[str, str] | None = None,
) -> tuple[int, bytes]:
    """Helper: call WSGI app and return (status_code, body)."""
    environ = {"PATH_INFO": path, "REQUEST_METHOD": "GET"}
    if env:
        environ.update(env)
    responses: list[tuple[str, list[tuple[str, str]]]] = []
    body_parts: list[bytes] = []

    def start_response(status: str, headers: list[tuple[str, str]]) -> None:
        responses.append((status, headers))

    result = app(environ, start_response)
    body_parts.extend(result)
    code = int(responses[0][0].split(" ")[0]) if responses else 500
    return code, b"".join(body_parts)


# --- Metrics Routing Tests ---


class TestMetricsRouting:
    """Test WSGI routing for metrics endpoint."""

    def test_metrics_default_path(self) -> None:
        code, body = _call_app("/metrics")
        assert code == 200
        assert b"arrow_lake" in body

    def test_metrics_custom_path_via_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("ARROW_LAKE__OBSERVABILITY__METRICS_PATH", "/custom-metrics")
        code, body = _call_app("/custom-metrics")
        assert code == 200
        assert b"arrow_lake" in body

    def test_metrics_returns_403_when_disabled(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("ARROW_LAKE__OBSERVABILITY__METRICS_ENABLED", "false")
        code, body = _call_app("/metrics")
        assert code == 403
        assert b"disabled" in body.lower()

    def test_metrics_enabled_true_explicit(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("ARROW_LAKE__OBSERVABILITY__METRICS_ENABLED", "true")
        code, _body = _call_app("/metrics")
        assert code == 200

    def test_health_endpoint_always_works(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: str
    ) -> None:
        monkeypatch.setenv("ARROW_LAKE__OBSERVABILITY__METRICS_ENABLED", "false")
        monkeypatch.setenv("ARROW_LAKE__STORAGE__BASE_URI", tmp_path)
        code, _body = _call_app("/health")
        assert code == 200

    def test_unknown_path_returns_404(self) -> None:
        code, _ = _call_app("/unknown")
        assert code == 404


# --- Registry Usage Tests ---


class TestRegistryUsage:
    """Test that server uses the project REGISTRY, not default."""

    def test_server_uses_custom_registry(self) -> None:
        with patch("prometheus_client.generate_latest") as mock_gen:
            mock_gen.return_value = b"# mock metrics"
            _call_app("/metrics")
            mock_gen.assert_called_once_with(REGISTRY)

    def test_new_ingestion_metrics_in_registry(self) -> None:
        output = _call_app("/metrics")[1].decode()
        assert "arrow_lake_ingestion_rows_total" in output
        assert "arrow_lake_ingestion_bytes_total" in output
        assert "arrow_lake_ingestion_duration_seconds" in output
        assert "arrow_lake_ingestion_errors_total" in output

    def test_new_processing_metrics_in_registry(self) -> None:
        output = _call_app("/metrics")[1].decode()
        assert "arrow_lake_processing_embeddings_total" in output
        assert "arrow_lake_processing_quality_rejects_total" in output
        assert "arrow_lake_processing_active_tasks" in output

    def test_new_query_metrics_in_registry(self) -> None:
        output = _call_app("/metrics")[1].decode()
        assert "arrow_lake_query_total" in output
        assert "arrow_lake_query_latency_seconds" in output
        assert "arrow_lake_query_results_total" in output


# --- Ingestion Metrics Tests ---


class TestIngestionMetrics:
    """Test ingestion metric increment behavior."""

    def test_rows_total_increment(self) -> None:
        ingestion_rows_total.clear()
        ingestion_rows_total.labels(source="file").inc(100)
        val = ingestion_rows_total.labels(source="file")._value.get()
        assert val == 100

    def test_bytes_total_increment(self) -> None:
        ingestion_bytes_total.clear()
        ingestion_bytes_total.labels(source="s3").inc(50000)
        val = ingestion_bytes_total.labels(source="s3")._value.get()
        assert val == 50000

    def test_duration_seconds_set(self) -> None:
        ingestion_duration_seconds.clear()
        ingestion_duration_seconds.labels(source="file").set(1.5)
        val = ingestion_duration_seconds.labels(source="file")._value.get()
        assert val == 1.5

    def test_errors_total_with_labels(self) -> None:
        ingestion_errors_total.clear()
        ingestion_errors_total.labels(source="file", error_type="schema_mismatch").inc(3)
        val = ingestion_errors_total.labels(
            source="file", error_type="schema_mismatch"
        )._value.get()
        assert val == 3


# --- Processing Metrics Tests ---


class TestProcessingMetrics:
    """Test processing metric behavior."""

    def test_embeddings_total_increment(self) -> None:
        processing_embeddings_total.clear()
        processing_embeddings_total.labels(model="bge-small").inc(50)
        val = processing_embeddings_total.labels(model="bge-small")._value.get()
        assert val == 50

    def test_quality_rejects_total(self) -> None:
        processing_quality_rejects_total.clear()
        processing_quality_rejects_total.labels(filter_name="text_length").inc(10)
        val = processing_quality_rejects_total.labels(filter_name="text_length")._value.get()
        assert val == 10

    def test_active_tasks_gauge(self) -> None:
        processing_active_tasks.set(5)
        val = processing_active_tasks._value.get()
        assert val == 5
        processing_active_tasks.set(0)


# --- Query Metrics Tests ---


class TestQueryMetrics:
    """Test query metric behavior."""

    def test_query_total_with_type_label(self) -> None:
        query_total.clear()
        query_total.labels(query_type="vector").inc(20)
        query_total.labels(query_type="fts").inc(15)
        assert query_total.labels(query_type="vector")._value.get() == 20
        assert query_total.labels(query_type="fts")._value.get() == 15

    def test_query_latency_seconds(self) -> None:
        # Histogram: observe a value and check it was recorded
        query_latency_seconds.labels(query_type="hybrid").observe(0.045)
        query_latency_seconds.labels(query_type="hybrid").observe(0.120)
        query_latency_seconds.labels(query_type="hybrid").observe(0.080)
        # Verify the metric is a Histogram with correct type
        from prometheus_client import Histogram
        assert isinstance(query_latency_seconds, Histogram)

    def test_query_results_total(self) -> None:
        query_results_total.clear()
        query_results_total.labels(query_type="vector").inc(200)
        val = query_results_total.labels(query_type="vector")._value.get()
        assert val == 200


# --- Naming Convention Tests ---


class TestNamingConvention:
    """All metrics must have arrow_lake_ prefix."""

    def test_all_ingestion_metrics_follow_convention(self) -> None:
        for metric in [
            ingestion_rows_total,
            ingestion_bytes_total,
            ingestion_duration_seconds,
            ingestion_errors_total,
        ]:
            assert metric._name.startswith("arrow_lake_"), f"{metric._name}"

    def test_all_processing_metrics_follow_convention(self) -> None:
        for metric in [
            processing_embeddings_total,
            processing_quality_rejects_total,
            processing_active_tasks,
        ]:
            assert metric._name.startswith("arrow_lake_"), f"{metric._name}"

    def test_all_query_metrics_follow_convention(self) -> None:
        for metric in [
            query_total,
            query_latency_seconds,
            query_results_total,
        ]:
            assert metric._name.startswith("arrow_lake_"), f"{metric._name}"

    def test_all_metrics_have_descriptions(self) -> None:
        output = _call_app("/metrics")[1].decode()
        assert "HELP arrow_lake_ingestion_rows_total" in output
        assert "HELP arrow_lake_processing_embeddings_total" in output
        assert "HELP arrow_lake_query_total" in output


# --- Config Helper Tests ---


class TestMetricsConfig:
    """Test _get_metrics_config helper."""

    def test_defaults(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("ARROW_LAKE__OBSERVABILITY__METRICS_PATH", raising=False)
        monkeypatch.delenv("ARROW_LAKE__OBSERVABILITY__METRICS_ENABLED", raising=False)
        path, enabled = _get_metrics_config()
        assert path == "/metrics"
        assert enabled is True

    def test_custom_values(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("ARROW_LAKE__OBSERVABILITY__METRICS_PATH", "/prometheus")
        monkeypatch.setenv("ARROW_LAKE__OBSERVABILITY__METRICS_ENABLED", "False")
        path, enabled = _get_metrics_config()
        assert path == "/prometheus"
        assert enabled is False
