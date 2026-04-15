"""E2E tests for HTTP API endpoints (Story 6.12, 7.8)."""

from __future__ import annotations

import os
import threading
from wsgiref.simple_server import make_server

import httpx
import pytest


@pytest.fixture()
def health_server(tmp_path: object) -> int:
    """Start the WSGI health server on a random port and return it."""
    from arrow_lake.server import app

    server = make_server("127.0.0.1", 0, app)
    port = server.socket.getsockname()[1]

    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield port
    server.shutdown()


class TestHealthEndpoint:
    """E2E: GET /health endpoint."""

    def test_health_returns_status(self, health_server: int) -> None:
        """GET /health returns 200 or 503 with JSON body."""
        resp = httpx.get(f"http://127.0.0.1:{health_server}/health")
        assert resp.status_code in (200, 503)
        body = resp.json()
        assert body["status"] in ("ok", "degraded")

    def test_health_contains_storage_field(self, health_server: int) -> None:
        """GET /health response includes storage status."""
        resp = httpx.get(f"http://127.0.0.1:{health_server}/health")
        body = resp.json()
        assert "storage" in body
        assert body["storage"] in ("accessible", "not_found", "error")

    def test_health_content_type_json(self, health_server: int) -> None:
        """GET /health returns application/json."""
        resp = httpx.get(f"http://127.0.0.1:{health_server}/health")
        assert resp.headers["content-type"] == "application/json"

    def test_health_with_existing_storage(self, health_server: int, tmp_path: object) -> None:
        """GET /health returns 'accessible' when storage dir exists."""
        original = os.environ.get("ARROW_LAKE__STORAGE__BASE_URI")
        os.environ["ARROW_LAKE__STORAGE__BASE_URI"] = str(tmp_path)
        try:
            resp = httpx.get(f"http://127.0.0.1:{health_server}/health")
            body = resp.json()
            assert body["storage"] == "accessible"
        finally:
            if original is None:
                os.environ.pop("ARROW_LAKE__STORAGE__BASE_URI", None)
            else:
                os.environ["ARROW_LAKE__STORAGE__BASE_URI"] = original


class TestMetricsEndpoint:
    """E2E: GET /metrics endpoint."""

    def test_metrics_returns_200(self, health_server: int) -> None:
        """GET /metrics returns 200 with Prometheus text format."""
        resp = httpx.get(f"http://127.0.0.1:{health_server}/metrics")
        assert resp.status_code == 200
        assert resp.headers["content-type"] == "text/plain"

    def test_metrics_contains_help_text(self, health_server: int) -> None:
        """GET /metrics output contains # HELP comments."""
        resp = httpx.get(f"http://127.0.0.1:{health_server}/metrics")
        assert "# HELP" in resp.text

    def test_metrics_disabled_returns_403(self, tmp_path: object) -> None:
        """GET /metrics returns 403 when metrics are disabled."""
        from arrow_lake.server import app

        os.environ["ARROW_LAKE__OBSERVABILITY__METRICS_ENABLED"] = "false"
        try:
            server = make_server("127.0.0.1", 0, app)
            port = server.socket.getsockname()[1]
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()

            resp = httpx.get(f"http://127.0.0.1:{port}/metrics")
            assert resp.status_code == 403
            assert resp.text == "Metrics disabled"
            server.shutdown()
        finally:
            os.environ.pop("ARROW_LAKE__OBSERVABILITY__METRICS_ENABLED", None)


class TestNotFoundEndpoint:
    """E2E: Unknown routes return 404."""

    def test_unknown_path_returns_404(self, health_server: int) -> None:
        """GET /unknown returns 404."""
        resp = httpx.get(f"http://127.0.0.1:{health_server}/unknown")
        assert resp.status_code == 404

    def test_api_prefix_returns_404(self, health_server: int) -> None:
        """GET /api/v1/anything returns 404 (no API routes yet)."""
        resp = httpx.get(f"http://127.0.0.1:{health_server}/api/v1/status")
        assert resp.status_code == 404
