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
    """Validate X-API-Key header on non-public endpoints (legacy).

    .. deprecated::
        Use ``api_key_middleware_fn`` with ``@app.middleware("http")`` instead.
        This class is retained for backward compatibility with tests.
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

        if request.method == "OPTIONS":
            return await call_next(request)

        if path in _PUBLIC_PATHS:
            return await call_next(request)

        if self.docs_enabled and path in _DOC_PATHS:
            return await call_next(request)

        if path == "/metrics":
            return await call_next(request)

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


async def api_key_middleware_fn(
    request: Request,
    call_next,
    *,
    api_key: str,
    header_name: str = "X-API-Key",
    docs_enabled: bool = True,
    default_role: str = "VIEWER",
) -> JSONResponse | None:
    """Pure ASGI API key middleware — correctly propagates request.state."""
    path = request.url.path

    if not api_key:
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

    if request.method == "OPTIONS":
        return await call_next(request)

    if path in _PUBLIC_PATHS:
        return await call_next(request)

    if docs_enabled and path in _DOC_PATHS:
        return await call_next(request)

    if path == "/metrics":
        return await call_next(request)

    provided = request.headers.get(header_name, "")
    if not hmac.compare_digest(provided, api_key):
        return JSONResponse(
            status_code=401,
            content={
                "success": False,
                "error": "UNAUTHORIZED",
                "message": "Missing or invalid API key",
            },
        )

    # Set user on request.state so downstream deps can read it
    from arrow_lake.api.auth_models import Role, TokenPayload

    role = Role(default_role) if default_role in Role.__members__ else Role.VIEWER
    request.state.user = TokenPayload(sub="api-key", role=role, exp=0, iat=0)

    return await call_next(request)
