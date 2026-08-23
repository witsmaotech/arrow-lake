"""API Key authentication middleware."""

from __future__ import annotations

import hmac

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

    # Static frontend prefix bypasses auth (login.html / assets load pre-auth).
    if path.startswith("/console") and "/api/" not in path:
        return await call_next(request)

    # v1.9.0: personal API token (libSQL identity_store).
    # Resolve BEFORE the shared-api_key gate so per-user tokens work even
    # when no global api_key is configured. The store is read lazily from
    # app.state (set by lifespan at startup, not available at middleware
    # registration time). A miss falls through to the shared-api_key path,
    # which still serves as the bootstrap/admin escape hatch.
    identity_store = getattr(request.app.state, "identity_store", None)
    if identity_store is not None:
        token = request.headers.get(header_name, "")
        if token:
            try:
                # v1.10.7 WP3 (review H5): libSQL read on EVERY token-bearing
                # request — off the event loop with a short timeout.
                from arrow_lake.api.utils import run_sync

                resolved = await run_sync(
                    identity_store.validate_token, token,
                    timeout=1.0, label="validate_token",
                )
            except Exception:  # noqa: BLE001 — fail-close handled by caller
                resolved = None
            if resolved is not None:
                from arrow_lake.api.auth_models import Role, TokenPayload

                role_name = str(resolved.get("role", "")).upper()
                if role_name not in Role.__members__:
                    return JSONResponse(
                        status_code=401,
                        content={"success": False, "error": "UNAUTHORIZED", "message": "Invalid token role"},
                    )
                role = Role[role_name]
                request.state.user = TokenPayload(
                    sub=str(resolved.get("username", "token")),
                    role=role,
                    # v1.10.5 M4: surface the token's scopes as the permissions
                    # claim so require_permission enforces them exactly; empty
                    # scopes fall back to the role hierarchy.
                    permissions=list(resolved.get("scopes") or []),
                    exp=0,
                    iat=0,
                )
                request.state.user_id = resolved.get("user_id")
                return await call_next(request)

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

    if path in (
        "/metrics",
        "/api/v1/auth/login",
        "/api/v1/auth/refresh",
        "/api/v1/auth/password-reset",  # v1.10.5 M1: one-time token is the credential
        "/api/v1/auth/jwks",  # v1.10.5 M3: public key is public (anonymous fetch)
    ):
        return await call_next(request)

    # Bearer JWT 请求交给下游 jwt 依赖验证(支持密码登录;api_key 层放行,符合 BOTH = Bearer OR X-API-Key)
    if request.headers.get("Authorization", "").startswith("Bearer "):
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

    role = Role[default_role.upper()] if default_role.upper() in Role.__members__ else Role.VIEWER
    request.state.user = TokenPayload(sub="api-key", role=role, exp=0, iat=0)

    return await call_next(request)
