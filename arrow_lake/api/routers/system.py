"""System endpoints: health probes and Prometheus metrics."""

from __future__ import annotations

import os

from fastapi import APIRouter, Depends, Request
from starlette.responses import JSONResponse, PlainTextResponse, Response

from arrow_lake.api.auth_models import Role
from arrow_lake.api.deps import get_app_config, require_role
from arrow_lake.config import ArrowLakeConfig
from arrow_lake.config.storage import StorageBackend

router = APIRouter(tags=["system"])


def _get_version() -> str:
    """Lazy-load version string."""
    try:
        from arrow_lake._version import __version__
        return __version__
    except ImportError:
        return ""


def _check_storage(config: ArrowLakeConfig) -> tuple[str, bool]:
    """Check storage accessibility. Returns (status_text, is_ok)."""
    storage = config.storage
    backend = storage.backend
    if backend != StorageBackend.LOCAL:
        try:
            import urllib.request

            endpoint = storage.s3_endpoint
            if endpoint:
                health_url = endpoint.rstrip("/") + "/minio/health/live"
                urllib.request.urlopen(health_url, timeout=3)
                return "accessible", True
            return "no_endpoint_configured", False
        except Exception:
            return "endpoint_unreachable", False
    base_uri = storage.base_uri
    if os.path.isdir(base_uri):
        return "accessible", True
    return "not_found", False


def _check_gravitino(uri: str) -> tuple[str, bool]:
    """Check Gravitino server health. Returns (status_text, is_ok)."""
    try:
        import urllib.request

        url = uri.rstrip("/") + "/api/metalakes"
        urllib.request.urlopen(url, timeout=3)
        return "healthy", True
    except Exception:
        return "unreachable", False


def _check_lance_rest(uri: str) -> tuple[str, bool]:
    """Check Lance REST Catalog health. Returns (status_text, is_ok)."""
    try:
        import urllib.request

        url = f"{uri.rstrip('/')}/v1/namespace/lance-catalog/list"
        # Disable proxy for internal service-to-service call
        handler = urllib.request.ProxyHandler({})
        opener = urllib.request.build_opener(handler)
        opener.open(url, timeout=3)
        return "healthy", True
    except Exception:
        return "unreachable", False


def _check_ray(ray_address: str) -> tuple[str, bool]:
    """Check Ray cluster health. Returns (status_text, is_ok)."""
    try:
        import ray
        if not ray.is_initialized():
            ray.init(address=ray_address, ignore_reinit_error=True, log_to_driver=False)
        return "healthy", True
    except Exception:
        return "unreachable", False


def _check_redis(redis_url: str) -> tuple[str, bool]:
    """Check Redis connectivity. Returns (status_text, is_ok)."""
    try:
        import redis as redis_lib
        client = redis_lib.from_url(redis_url, socket_timeout=3)
        client.ping()
        return "healthy", True
    except Exception:
        return "unreachable", False


@router.get("/health/live", summary="Liveness probe")
async def health_live() -> dict:
    """Lightweight liveness check — returns 200 if process is running."""
    return {"status": "ok"}


def _attach_pool_stats(status: dict, request: Request) -> None:
    """Append DuckDB session pool stats to *status* if available.

    Non-fatal: health endpoint must not fail if session manager is unavailable.
    """
    from unittest.mock import MagicMock

    lake = getattr(request.app.state, "lake", None)
    if lake and not isinstance(lake, MagicMock):
        try:
            stats = lake.get_session_manager().get_stats()
            status["duckdb_pool"] = {
                "pool_size": stats.pool_size,
                "active_sessions": stats.active_sessions,
                "queued_requests": stats.queued_requests,
                "total_queries": stats.total_queries,
                "total_errors": stats.total_errors,
            }
        except Exception:
            pass  # Non-fatal: health endpoint must not fail


@router.get("/health/ready", summary="Readiness probe")
async def health_ready(
    request: Request,
    config: ArrowLakeConfig = Depends(get_app_config),
) -> Response:
    """Readiness check — verifies storage and dependencies are accessible."""
    status: dict = {"status": "ok", "version": _get_version()}
    storage_text, storage_ok = _check_storage(config)
    status["storage"] = storage_text
    if not storage_ok:
        status["status"] = "degraded"

    # Dependency probes (non-fatal — informational only)
    if config.gravitino.enabled:
        grav_text, _grav_ok = _check_gravitino(config.gravitino.uri)
        status["gravitino"] = grav_text
    if hasattr(config, "compute") and getattr(config.compute, "ray_address", ""):
        ray_text, _ = _check_ray(config.compute.ray_address)
        status["ray"] = ray_text
    if hasattr(config, "redis") and getattr(config.redis, "enabled", False):
        redis_text, _ = _check_redis(config.redis.url)
        status["redis"] = redis_text

    if request is not None:
        _attach_pool_stats(status, request)
    http_code = 200 if status["status"] == "ok" else 503
    _attach_pool_stats(status, request)
    return JSONResponse(content=status, status_code=http_code)


@router.get("/health", summary="Health check (backward compatible)")
async def health_check(
    request: Request,
    config: ArrowLakeConfig = Depends(get_app_config),
) -> Response:
    """Return service health status. Checks storage accessibility.

    Kept for backward compatibility. Prefer /health/live and /health/ready.
    """
    status: dict = {"status": "ok", "version": _get_version()}
    storage_text, storage_ok = _check_storage(config)
    status["storage"] = storage_text
    if not storage_ok:
        status["status"] = "degraded"

    # Gravitino health (non-fatal — optional dependency)
    if config.gravitino.enabled:
        grav_text, _grav_ok = _check_gravitino(config.gravitino.uri)
        status["gravitino"] = grav_text
        if config.gravitino.lance_rest_enabled:
            lr_text, _lr_ok = _check_lance_rest(config.gravitino.lance_rest_uri)
            status["lance_rest"] = lr_text

    _attach_pool_stats(status, request)
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
async def version_info(_user: dict = Depends(require_role(Role.VIEWER))) -> dict:
    """Return version, Python version, and optional dependency versions."""
    result: dict = {"version": ""}

    try:
        from arrow_lake._version import __version__
        result["version"] = __version__
    except ImportError:
        pass

    import sys
    result["python"] = f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}"

    # Check optional dependencies
    deps = ["fastapi", "uvicorn", "pyarrow", "duckdb", "daft", "httpx"]
    for dep in deps:
        try:
            import importlib.metadata as im
            result[dep] = im.version(dep)
        except ImportError:
            result[dep] = "not installed"

    return result
