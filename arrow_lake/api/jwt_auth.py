"""JWT authentication middleware (pure ASGI).

Uses @app.middleware("http") instead of BaseHTTPMiddleware to correctly
propagate request.state between middleware layers.

Usage in app.py::

    @app.middleware("http")
    async def jwt_auth_middleware(request, call_next):
        return await jwt_auth_middleware_fn(request, call_next, auth_service)
"""

from __future__ import annotations

import logging

from starlette.requests import Request
from starlette.responses import JSONResponse, Response

logger = logging.getLogger(__name__)

# Path prefixes that always bypass JWT authentication.
_JWT_PUBLIC_PREFIXES: tuple[str, ...] = (
    "/health",
    "/metrics",
)

# Doc path prefixes that bypass JWT only when docs are enabled.
_JWT_DOC_PREFIXES: tuple[str, ...] = (
    "/docs",
    "/redoc",
    "/openapi.json",
)


async def jwt_auth_middleware_fn(
    request: Request, call_next, auth_service, *, docs_enabled: bool = True,
) -> Response:
    """Pure ASGI JWT authentication middleware function.

    Validates JWT from Authorization header and sets request.state.user.
    """
    path = request.url.path

    # OPTIONS preflight requests bypass auth
    if request.method == "OPTIONS":
        return await call_next(request)

    # Public path prefixes bypass auth
    if any(path.startswith(prefix) for prefix in _JWT_PUBLIC_PREFIXES):
        return await call_next(request)

    # Doc paths bypass auth only when docs are enabled
    if docs_enabled and (
        path in ("/docs", "/redoc", "/openapi.json") or path.startswith(("/docs/", "/redoc/"))
    ):
        return await call_next(request)

    # Auth endpoints bypass JWT (they use API key to get JWT)
    if path == "/api/v1/auth/token" or path == "/api/v1/auth/refresh":
        return await call_next(request)

    # Extract Bearer token
    auth_header = request.headers.get("Authorization", "")
    if not auth_header.startswith("Bearer "):
        return JSONResponse(
            status_code=401,
            content={
                "success": False,
                "error": "AUTH_INVALID_TOKEN",
                "message": "Missing or invalid Authorization header",
            },
        )

    token = auth_header[7:]  # strip "Bearer "
    try:
        payload = auth_service.verify_token(token)
        request.state.user = payload
    except ValueError as exc:
        msg = str(exc)
        logger.debug("JWT verification failed: %s", msg)
        if "expired" in msg.lower():
            return JSONResponse(
                status_code=401,
                content={
                    "success": False,
                    "error": "AUTH_TOKEN_EXPIRED",
                    "message": "Token has expired",
                },
            )
        return JSONResponse(
            status_code=401,
            content={
                "success": False,
                "error": "AUTH_INVALID_TOKEN",
                "message": "Invalid or malformed token",
            },
        )

    response = await call_next(request)
    return response
