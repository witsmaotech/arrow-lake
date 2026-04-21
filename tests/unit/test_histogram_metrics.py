"""Unit tests for Histogram metrics migration and new metrics."""

from __future__ import annotations

from prometheus_client import Histogram

from arrow_lake.core.metrics import (
    auth_requests_total,
    http_request_duration_seconds,
    query_latency_seconds,
    REGISTRY,
)


def test_query_latency_is_histogram() -> None:
    """query_latency_seconds should be a Histogram (not Gauge)."""
    assert isinstance(query_latency_seconds, Histogram)


def test_query_latency_has_buckets() -> None:
    """query_latency_seconds should have latency-appropriate bucket boundaries."""
    # Histogram in prometheus_client registers bucket/count/sum metrics
    metric_names = list(REGISTRY._names_to_collectors.keys())
    assert "arrow_lake_query_latency_seconds_bucket" in metric_names
    assert "arrow_lake_query_latency_seconds_count" in metric_names
    assert "arrow_lake_query_latency_seconds_sum" in metric_names


def test_http_request_duration_is_histogram() -> None:
    """http_request_duration_seconds should be a Histogram."""
    assert isinstance(http_request_duration_seconds, Histogram)


def test_http_request_duration_labels() -> None:
    """http_request_duration_seconds should have method, path, and status_code labels."""
    assert "method" in http_request_duration_seconds._labelnames
    assert "path" in http_request_duration_seconds._labelnames
    assert "status_code" in http_request_duration_seconds._labelnames


def test_auth_requests_total_is_counter() -> None:
    """auth_requests_total should be a Counter with auth_method and status labels."""
    from prometheus_client import Counter
    assert isinstance(auth_requests_total, Counter)
    assert "auth_method" in auth_requests_total._labelnames
    assert "status" in auth_requests_total._labelnames
