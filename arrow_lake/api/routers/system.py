"""System endpoints: health check and Prometheus metrics."""

from __future__ import annotations

from fastapi import APIRouter, Depends
from starlette.responses import JSONResponse, PlainTextResponse, Response

from arrow_lake.api.deps import get_config
from arrow_lake.config import ArrowLakeConfig

router = APIRouter(tags=["system"])


@router.get("/health", summary="Health check")
async def health_check(config: ArrowLakeConfig = Depends(get_config)) -> dict:
    """Return service health status.

    Checks storage accessibility and catalog availability.
    """
    status: dict = {"status": "ok", "version": ""}

    # Import version lazily
    try:
        from arrow_lake._version import __version__
        status["version"] = __version__
    except Exception:
        pass

    # Check storage accessibility
    import os
    base_uri = config.storage.base_uri
    if base_uri.startswith("s3://"):
        # S3/MinIO backend — check via MinIO health endpoint
        try:
            import urllib.request
            endpoint = config.storage.s3_endpoint
            if endpoint:
                health_url = endpoint.rstrip("/") + "/minio/health/live"
                urllib.request.urlopen(health_url, timeout=3)
                status["storage"] = "accessible"
            else:
                status["storage"] = "no_endpoint_configured"
                status["status"] = "degraded"
        except Exception:
            status["storage"] = "endpoint_unreachable"
            status["status"] = "degraded"
    elif os.path.isdir(base_uri):
        status["storage"] = "accessible"
    else:
        status["storage"] = "not_found"
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
