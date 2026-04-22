"""Health check endpoint for production deployments (Story 6.12, 7.8).

Provides a lightweight HTTP server with:
- GET /health — returns JSON health status
- GET {metrics_path} — Prometheus metrics endpoint (default /metrics)

.. deprecated::
    ``arrow_lake.server`` is deprecated since v0.2.0.
    Use ``uvicorn arrow_lake.api.app:create_app --factory`` instead.

Usage as standalone::

    python -m arrow_lake.server --port 8000

Usage with gunicorn::

    gunicorn arrow_lake.server:app --bind 0.0.0.0:8000
"""

from __future__ import annotations

import warnings

warnings.warn(
    "arrow_lake.server is deprecated since v0.2.0. "
    "Use 'uvicorn arrow_lake.api.app:create_app --factory' instead.",
    DeprecationWarning,
    stacklevel=2,
)

import json
import os
from typing import Any
from wsgiref.simple_server import make_server

import structlog

_log = structlog.get_logger(__name__)


def health_response() -> tuple[dict[str, Any], int]:
    """Build health check response.

    Returns:
        (response_body, http_status_code)
    """
    status: dict[str, Any] = {"status": "ok"}

    # Check storage accessibility
    try:
        base_uri = os.environ.get("ARROW_LAKE__STORAGE__BASE_URI", "./data/lake")
        if os.path.isdir(base_uri):
            status["storage"] = "accessible"
        else:
            status["storage"] = "not_found"
            status["status"] = "degraded"
    except (OSError, ImportError):
        status["storage"] = "error"
        status["status"] = "degraded"

    # Check catalog (placeholder — in production checks Ray GCS)
    ray_address = os.environ.get("ARROW_LAKE__COMPUTE__RAY_ADDRESS", "")
    if ray_address and ray_address != "auto":
        try:
            import ray

            if ray.is_initialized():
                status["catalog"] = "available"
            else:
                status["catalog"] = "ray_not_initialized"
                status["status"] = "degraded"
        except (ImportError, Exception):
            status["catalog"] = "unavailable"
            status["status"] = "degraded"
    else:
        status["catalog"] = "available"

    http_code = 200 if status["status"] == "ok" else 503
    return status, http_code


def _get_metrics_config() -> tuple[str, bool]:
    """Read metrics path and enabled flag from environment.

    Returns:
        (metrics_path, metrics_enabled)
    """
    metrics_path = os.environ.get("ARROW_LAKE__OBSERVABILITY__METRICS_PATH", "/metrics")
    metrics_enabled = (
        os.environ.get("ARROW_LAKE__OBSERVABILITY__METRICS_ENABLED", "true").lower() == "true"
    )
    return metrics_path, metrics_enabled


# --- WSGI App (no external dependencies) ---


def app(environ: dict[str, str], start_response: Any) -> Any:
    """Simple WSGI app for health and metrics."""

    path = environ.get("PATH_INFO", "/")

    if path == "/health":
        body, code = health_response()
        start_response(
            f"{code} OK" if code == 200 else f"{code} Service Unavailable",
            [("Content-Type", "application/json")],
        )
        return [json.dumps(body).encode()]

    metrics_path, metrics_enabled = _get_metrics_config()

    if path == metrics_path:
        if not metrics_enabled:
            start_response("403 Forbidden", [("Content-Type", "text/plain")])
            return [b"Metrics disabled"]
        try:
            from prometheus_client import generate_latest

            from arrow_lake.core.metrics import REGISTRY
        except ImportError:
            start_response("503 Service Unavailable", [("Content-Type", "text/plain")])
            return [b"prometheus_client not installed"]
        start_response("200 OK", [("Content-Type", "text/plain")])
        return [generate_latest(REGISTRY)]

    start_response("404 Not Found", [("Content-Type", "text/plain")])
    return [b"Not Found"]


def run_server(host: str = "0.0.0.0", port: int = 8000) -> None:
    """Run the health check server (blocking)."""
    server = make_server(host, port, app)
    _log.info("health_server_started", host=host, port=port)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        _log.info("health_server_shutting_down")
        server.shutdown()


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Arrow Lake health check server")
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args()
    run_server(args.host, args.port)
