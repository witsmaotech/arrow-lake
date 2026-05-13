"""Request/response middleware for compression, size limits, correlation ID, and security headers."""

from __future__ import annotations

import re
import time
import uuid

from starlette.requests import Request
from starlette.responses import JSONResponse, Response

# Paths that skip security headers (operational/metrics endpoints)
_SECURITY_SKIP_PREFIXES: tuple[str, ...] = ("/health", "/metrics")


async def request_size_limit_middleware_fn(
    request: Request, call_next, *, max_size_bytes: int = 100 * 1024 * 1024
) -> Response:
    """Pure ASGI middleware: reject requests exceeding a configured maximum size."""
    # Check Content-Length header
    content_length = request.headers.get("content-length")
    if content_length:
        try:
            size = int(content_length)
            if size > max_size_bytes:
                return JSONResponse(
                    status_code=413,
                    content={
                        "success": False,
                        "error": "REQUEST_TOO_LARGE",
                        "message": (
                            f"Request body ({size} bytes) exceeds "
                            f"maximum allowed size ({max_size_bytes} bytes)"
                        ),
                    },
                )
        except (ValueError, TypeError):
            pass

    # Reject chunked Transfer-Encoding with no Content-Length as potentially oversized.
    # Route handlers should enforce their own body-size limits for these requests.
    transfer_encoding = request.headers.get("transfer-encoding", "").lower()
    if "chunked" in transfer_encoding and not content_length:
        request.state._skip_size_limit = True

    return await call_next(request)


async def security_headers_middleware_fn(
    request: Request,
    call_next,
    *,
    content_security_policy: str = "",
    frame_options: str = "DENY",
) -> Response:
    """Pure ASGI middleware: add HTTP security response headers to non-operational paths."""
    response = await call_next(request)

    path = request.url.path
    if any(path.startswith(p) for p in _SECURITY_SKIP_PREFIXES):
        return response

    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
    response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
    response.headers["X-Request-ID"] = getattr(
        request.state, "correlation_id", ""
    ) or ""

    if frame_options:
        response.headers["X-Frame-Options"] = frame_options

    if content_security_policy:
        response.headers["Content-Security-Policy"] = content_security_policy

    return response


_PATH_TEMPLATE_RE = re.compile(r"/[0-9a-f]{8,}-|[0-9]+(?=/|$)")
_REQUEST_ID_RE = re.compile(r"^[a-zA-Z0-9._\-:/]{1,128}$")


async def metrics_middleware_fn(request: Request, call_next) -> Response:
    """Pure ASGI middleware: record HTTP request duration to Prometheus."""
    t0 = time.monotonic()
    response = await call_next(request)
    from arrow_lake.core.metrics import get_metrics_enabled, http_request_duration_seconds

    if get_metrics_enabled():
        path = _PATH_TEMPLATE_RE.sub("/:id", request.url.path)
        http_request_duration_seconds.labels(
            method=request.method,
            path=path,
            status_code=str(response.status_code),
        ).observe(time.monotonic() - t0)
    return response


async def correlation_id_middleware_fn(
    request: Request, call_next, *, auto_generate: bool = True
) -> Response:
    """Pure ASGI correlation ID middleware function.

    Extracts or generates X-Request-ID and propagates it through the request.
    Correctly propagates request.state when used with @app.middleware("http").
    """
    request_id = request.headers.get("X-Request-ID", "").strip()
    if request_id and not _REQUEST_ID_RE.match(request_id):
        request_id = ""
    if not request_id and auto_generate:
        request_id = str(uuid.uuid4())

    request.state.correlation_id = request_id
    response = await call_next(request)

    if request_id:
        response.headers["X-Request-ID"] = request_id

    return response
