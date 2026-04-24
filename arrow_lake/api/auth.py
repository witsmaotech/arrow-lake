"""API Key authentication middleware."""

from __future__ import annotations

import hmac

from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import JSONResponse

# Paths that bypass API key authentication.
_PUBLIC_PATHS: frozenset[str] = frozenset({
    "/health",
    "/health/live",
    "/health/ready",
})

# Doc paths that bypass auth only when docs are enabled.
_DOC_PATHS: frozenset[str] = frozenset({
    "/openapi.json",
    "/docs",
    "/redoc",
    "/docs/oauth2-redirect",
})


class ApiKeyMiddleware(BaseHTTPMiddleware):
    """Validate X-API-Key header on non-public endpoints.

    When ``api_key`` is empty, authentication is disabled entirely.
    """

    def __init__(
        self,
        app,
        api_key: str,
        header_name: str = "X-API-Key",
        docs_enabled: bool = True,
    ) -> None:
        super().__init__(app)
        self.api_key = api_key
        self.header_name = header_name
        self.docs_enabled = docs_enabled

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint):
        # No key configured — reject authenticated requests.
        # (Startup validation in app.py should prevent this from happening,
        # but defense-in-depth ensures auth can never be silently disabled.)
        if not self.api_key:
            path = request.url.path
            if path in _PUBLIC_PATHS or request.method == "OPTIONS" or path == "/metrics":
                return await call_next(request)
            return JSONResponse(
                status_code=401,
                content={
                    "success": False,
                    "error": "UNAUTHORIZED",
                    "message": "API authentication not configured",
                },
            )

        path = request.url.path

        # OPTIONS preflight requests bypass auth (required by CORS)
        if request.method == "OPTIONS":
            return await call_next(request)

        # Public paths bypass auth
        if path in _PUBLIC_PATHS:
            return await call_next(request)

        # Doc paths bypass auth only when docs are enabled
        if self.docs_enabled and path in _DOC_PATHS:
            return await call_next(request)

        # Allow /metrics when metrics_path is configured
        if path == "/metrics":
            return await call_next(request)

        # Validate API key (constant-time comparison to prevent timing attacks)
        provided = request.headers.get(self.header_name, "")
        if not hmac.compare_digest(provided, self.api_key):
            return JSONResponse(
                status_code=401,
                content={
                    "success": False,
                    "error": "UNAUTHORIZED",
                    "message": "Missing or invalid API key",
                },
            )

        return await call_next(request)
