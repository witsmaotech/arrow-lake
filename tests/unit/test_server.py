"""Tests for server.py — health_response, _get_metrics_config, and WSGI app."""

from __future__ import annotations

import json
import warnings
from unittest.mock import patch

import pytest


# Suppress the deprecation warning from module import
with warnings.catch_warnings():
    warnings.simplefilter("ignore", DeprecationWarning)
    from arrow_lake.server import (
        _get_metrics_config,
        app as wsgi_app,
        health_response,
    )


# ===========================================================================
# health_response
# ===========================================================================


class TestHealthResponse:
    def test_ok_with_tmp_dir(self, tmp_path) -> None:
        with patch.dict("os.environ", {"ARROW_LAKE__STORAGE__BASE_URI": str(tmp_path)}):
            status, code = health_response()
        assert code == 200
        assert status["status"] == "ok"
        assert status["storage"] == "accessible"

    def test_degraded_when_storage_missing(self) -> None:
        with patch.dict("os.environ", {"ARROW_LAKE__STORAGE__BASE_URI": "/nonexistent_dir_for_test"}):
            status, code = health_response()
        assert code == 503
        assert status["status"] == "degraded"
        assert status["storage"] == "not_found"

    def test_catalog_available_when_no_ray(self) -> None:
        with patch.dict("os.environ", {
            "ARROW_LAKE__STORAGE__BASE_URI": "/tmp",
            "ARROW_LAKE__COMPUTE__RAY_ADDRESS": "",
        }):
            status, code = health_response()
        assert status["catalog"] == "available"

    def test_catalog_auto_treated_as_available(self) -> None:
        with patch.dict("os.environ", {
            "ARROW_LAKE__STORAGE__BASE_URI": "/tmp",
            "ARROW_LAKE__COMPUTE__RAY_ADDRESS": "auto",
        }):
            status, code = health_response()
        assert status["catalog"] == "available"

    def test_ray_not_initialized(self) -> None:
        with patch.dict("os.environ", {
            "ARROW_LAKE__STORAGE__BASE_URI": "/tmp",
            "ARROW_LAKE__COMPUTE__RAY_ADDRESS": "ray://head:10001",
        }):
            with patch("arrow_lake.server.ray", create=True) as mock_ray:
                mock_ray.is_initialized.return_value = False
                # Need to make import succeed
                with patch.dict("sys.modules", {"ray": mock_ray}):
                    status, code = health_response()
        assert code == 503
        assert status["catalog"] == "ray_not_initialized"


# ===========================================================================
# _get_metrics_config
# ===========================================================================


class TestGetMetricsConfig:
    def test_defaults(self) -> None:
        with patch.dict("os.environ", {}, clear=False):
            path, enabled = _get_metrics_config()
        assert path == "/metrics"
        assert enabled is True

    def test_custom_path(self) -> None:
        with patch.dict("os.environ", {"ARROW_LAKE__OBSERVABILITY__METRICS_PATH": "/custom"}):
            path, enabled = _get_metrics_config()
        assert path == "/custom"

    def test_disabled(self) -> None:
        with patch.dict("os.environ", {"ARROW_LAKE__OBSERVABILITY__METRICS_ENABLED": "false"}):
            _, enabled = _get_metrics_config()
        assert enabled is False


# ===========================================================================
# WSGI app
# ===========================================================================


class TestWSGIApp:
    @staticmethod
    def _call_app(path: str, environ_overrides: dict | None = None):
        """Helper to call the WSGI app and capture response."""
        environ = {"PATH_INFO": path}
        if environ_overrides:
            environ.update(environ_overrides)

        status_line = None
        headers = None

        def start_response(s, h):
            nonlocal status_line, headers
            status_line = s
            headers = h

        body = wsgi_app(environ, start_response)
        return status_line, headers, body

    def test_health_endpoint(self, tmp_path) -> None:
        with patch.dict("os.environ", {"ARROW_LAKE__STORAGE__BASE_URI": str(tmp_path)}):
            status_line, headers, body = self._call_app("/health")
        assert status_line.startswith("200")
        data = json.loads(body[0])
        assert data["status"] == "ok"

    def test_health_degraded(self) -> None:
        with patch.dict("os.environ", {"ARROW_LAKE__STORAGE__BASE_URI": "/nonexistent"}):
            status_line, _, body = self._call_app("/health")
        assert "503" in status_line
        data = json.loads(body[0])
        assert data["status"] == "degraded"

    def test_metrics_disabled_returns_403(self) -> None:
        with patch.dict("os.environ", {"ARROW_LAKE__OBSERVABILITY__METRICS_ENABLED": "false"}):
            status_line, _, _ = self._call_app("/metrics")
        assert "403" in status_line

    def test_unknown_path_returns_404(self) -> None:
        status_line, _, _ = self._call_app("/unknown")
        assert "404" in status_line
