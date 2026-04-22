"""Request/response middleware for compression, size limits, correlation ID, and security headers."""

from __future__ import annotations

import re
import time
import uuid

from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

# Paths that skip security headers (operational/metrics endpoints)
_SECURITY_SKIP_PREFIXES: tuple[str, ...] = ("/health", "/metrics")


class RequestSizeLimitMiddleware(BaseHTTPMiddleware):
    """Reject requests whose body exceeds a configured maximum size."""

    def __init__(self, app, max_size_bytes: int = 100 * 1024 * 1024) -> None:
        super().__init__(app)
        self.max_size_bytes = max_size_bytes

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint):
        content_length = request.headers.get("content-length")
        if content_length:
            try:
                size = int(content_length)
                if size > self.max_size_bytes:
                    return JSONResponse(
                        status_code=413,
                        content={
                            "success": False,
                            "error": "REQUEST_TOO_LARGE",
                            "message": (
                                f"Request body ({size} bytes) exceeds "
                                f"maximum allowed size ({self.max_size_bytes} bytes)"
                            ),
                        },
                    )
            except (ValueError, TypeError):
                pass

        return await call_next(request)


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """Add HTTP security response headers to non-operational paths."""

    def __init__(
        self,
        app,
        *,
        content_security_policy: str = "",
        frame_options: str = "DENY",
    ) -> None:
        super().__init__(app)
        self.content_security_policy = content_security_policy
        self.frame_options = frame_options

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint):
        response = await call_next(request)

        path = request.url.path
        if any(path.startswith(p) for p in _SECURITY_SKIP_PREFIXES):
            return response

        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        response.headers["X-Request-ID"] = getattr(
            request.state, "correlation_id", ""
        ) or ""

        if self.frame_options:
            response.headers["X-Frame-Options"] = self.frame_options

        if self.content_security_policy:
            response.headers["Content-Security-Policy"] = self.content_security_policy

        return response


_PATH_TEMPLATE_RE = re.compile(r"/[0-9a-f]{8,}-|[0-9]+(?=/|$)")


class MetricsMiddleware(BaseHTTPMiddleware):
    """Record HTTP request duration to Prometheus."""

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint):
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
    request_id = request.headers.get("X-Request-ID")
    if not request_id and auto_generate:
        request_id = str(uuid.uuid4())

    request.state.correlation_id = request_id
    response = await call_next(request)

    if request_id:
        response.headers["X-Request-ID"] = request_id

    return response
