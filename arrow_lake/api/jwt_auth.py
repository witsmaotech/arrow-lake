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
    "/console",  # SQL Worksheet static frontend (login.html / assets must load pre-auth)
)

# Doc path prefixes that bypass JWT only when docs are enabled.
_JWT_DOC_PREFIXES: tuple[str, ...] = (
    "/docs",
    "/redoc",
    "/openapi.json",
)


async def jwt_auth_middleware_fn(
    request: Request, call_next, auth_service, *, docs_enabled: bool = True,
    api_key_header: str | None = None,
) -> Response:
    """Pure ASGI JWT authentication middleware function.

    Validates JWT from Authorization header and sets request.state.user.

    ``api_key_header`` (set only in auth_mode=both with the api-key middleware
    active) delegates header-carrying, Bearer-less requests to that inner
    middleware — it is the authority for the shared key / alp_ personal-token
    scheme, so "Bearer OR X-API-Key" holds instead of the JWT layer rejecting
    the request before the api-key layer ever runs.
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

    # Auth endpoints bypass JWT (they authenticate by their own means:
    # API key / bootstrap token / the one-time reset token itself)
    if path in (
        "/api/v1/auth/token",
        "/api/v1/auth/refresh",
        "/api/v1/auth/login",  # v1.10.5 M1: was missing — blocked password login in jwt/both mode
        "/api/v1/auth/password-reset",  # v1.10.5 M1: the reset token IS the credential
        "/api/v1/auth/jwks",  # v1.10.5 M3: public key is public (anonymous fetch)
    ):
        return await call_next(request)

    # Extract Bearer token
    auth_header = request.headers.get("Authorization", "")
    if not auth_header.startswith("Bearer "):
        # auth_mode=both: a request carrying the API-key header (shared key or
        # alp_ personal token) authenticates via the inner api-key middleware —
        # delegate to it instead of rejecting here. An INVALID key still 401s,
        # the inner middleware is the authority for that scheme.
        if api_key_header and request.headers.get(api_key_header, ""):
            return await call_next(request)
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
        # v1.10.7 WP3 (review H5): verify_token touches the redis blacklist
        # (EXISTS) and the libSQL token_valid_after provider — run it off the
        # event loop so one slow storage call can't stall this worker.
        from arrow_lake.api.utils import run_sync

        payload = await run_sync(
            auth_service.verify_token, token, timeout=1.0, label="verify_token"
        )
        request.state.user = payload
    except TimeoutError as exc:
        # M-7 (review): a storage timeout is a dependency failure, not an
        # invalid token — 503 lets clients retry instead of forcing re-login
        # on every transient redis/libSQL hiccup.
        logger.warning("JWT verification timed out: %s", exc)
        return JSONResponse(
            status_code=503,
            content={
                "success": False,
                "error": "AUTH_STORE_UNAVAILABLE",
                "message": "Token verification timed out — retry shortly",
            },
        )
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
