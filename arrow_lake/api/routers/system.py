"""System endpoints: health probes and Prometheus metrics."""

from __future__ import annotations

import os

from fastapi import APIRouter, Depends
from starlette.responses import JSONResponse, PlainTextResponse, Response

from arrow_lake.api.deps import get_app_config
from arrow_lake.config import ArrowLakeConfig

router = APIRouter(tags=["system"])


def _get_version() -> str:
    """Lazy-load version string."""
    try:
        from arrow_lake._version import __version__
        return __version__
    except Exception:
        return ""


def _check_storage(config: ArrowLakeConfig) -> tuple[str, bool]:
    """Check storage accessibility. Returns (status_text, is_ok)."""
    base_uri = config.storage.base_uri
    if base_uri.startswith("s3://"):
        try:
            import urllib.request

            endpoint = config.storage.s3_endpoint
            if endpoint:
                health_url = endpoint.rstrip("/") + "/minio/health/live"
                urllib.request.urlopen(health_url, timeout=3)
                return "accessible", True
            return "no_endpoint_configured", False
        except Exception:
            return "endpoint_unreachable", False
    if os.path.isdir(base_uri):
        return "accessible", True
    return "not_found", False


@router.get("/health/live", summary="Liveness probe")
async def health_live() -> dict:
    """Lightweight liveness check — returns 200 if process is running."""
    return {"status": "ok"}


@router.get("/health/ready", summary="Readiness probe")
async def health_ready(config: ArrowLakeConfig = Depends(get_app_config)) -> Response:
    """Readiness check — verifies storage and dependencies are accessible."""
    status: dict = {"status": "ok", "version": _get_version()}
    storage_text, storage_ok = _check_storage(config)
    status["storage"] = storage_text
    if not storage_ok:
        status["status"] = "degraded"
    http_code = 200 if status["status"] == "ok" else 503
    return JSONResponse(content=status, status_code=http_code)


@router.get("/health", summary="Health check (backward compatible)")
async def health_check(config: ArrowLakeConfig = Depends(get_app_config)) -> Response:
    """Return service health status. Checks storage accessibility.

    Kept for backward compatibility. Prefer /health/live and /health/ready.
    """
    status: dict = {"status": "ok", "version": _get_version()}
    storage_text, storage_ok = _check_storage(config)
    status["storage"] = storage_text
    if not storage_ok:
        status["status"] = "degraded"
    http_code = 200 if status["status"] == "ok" else 503
    return JSONResponse(content=status, status_code=http_code)


@router.get("/metrics", summary="Prometheus metrics")
async def metrics() -> Response:
    """Return Prometheus-formatted metrics."""
    try:
        from prometheus_client import generate_latest

        from arrow_lake.core.metrics import REGISTRY
    except ImportError:
        return PlainTextResponse(
            "prometheus_client not installed",
            status_code=503,
            media_type="text/plain",
        )

    return PlainTextResponse(
        generate_latest(REGISTRY).decode(),
        media_type="text/plain",
    )


@router.get("/api/v1/version", summary="Version and dependencies")
async def version_info() -> dict:
    """Return version, Python version, and optional dependency versions."""
    result: dict = {"version": ""}

    try:
        from arrow_lake._version import __version__
        result["version"] = __version__
    except Exception:
        pass

    import sys
    result["python"] = f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}"

    # Check optional dependencies
    deps = ["fastapi", "uvicorn", "pyarrow", "duckdb", "daft", "httpx"]
    for dep in deps:
        try:
            import importlib.metadata as im
            result[dep] = im.version(dep)
        except Exception:
            result[dep] = "not installed"

    return result
