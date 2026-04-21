"""JWT authentication middleware (pure ASGI).

Uses @app.middleware("http") instead of BaseHTTPMiddleware to correctly
propagate request.state between middleware layers.

Usage in app.py::

    @app.middleware("http")
    async def jwt_auth_middleware(request, call_next):
        return await jwt_auth_middleware_fn(request, call_next, auth_service)
"""

from __future__ import annotations

from starlette.requests import Request
from starlette.responses import JSONResponse, Response

# Path prefixes that bypass JWT authentication.
_JWT_PUBLIC_PREFIXES: tuple[str, ...] = (
    "/health",
    "/docs",
    "/redoc",
    "/openapi.json",
    "/metrics",
)


async def jwt_auth_middleware_fn(
    request: Request, call_next, auth_service
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

    # Auth endpoints bypass JWT (they use API key to get JWT)
    if path.startswith("/api/v2/auth/"):
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
                "message": msg,
            },
        )

    response = await call_next(request)
    return response
