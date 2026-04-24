"""Dependency injection for the REST API.

Provides FastAPI Depends-callable factories for Lake instance, config,
and auth (current user, role-based access).
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timezone
from functools import lru_cache

from fastapi import HTTPException, Request

from arrow_lake.api.auth_models import Role, TokenPayload
from arrow_lake.config import ArrowLakeConfig


@lru_cache(maxsize=1)
def get_config() -> ArrowLakeConfig:
    """Return cached application config (singleton per process)."""
    return ArrowLakeConfig()


def get_app_config(request: Request) -> ArrowLakeConfig:
    """Return the config bound to this app instance (set in create_app).

    Use this when you need the per-app config (e.g., with dependency overrides
    in tests), rather than the cached singleton from get_config().
    """
    return request.app.state.config


def get_lake(request: Request):
    """Return the Lake instance bound to this app (set in lifespan)."""
    return request.app.state.lake


def get_current_user(request: Request) -> TokenPayload:
    """Return the authenticated user.

    Tries request.state.user first.  Falls back to verifying the Bearer
    token directly via the AuthService singleton (needed because
    BaseHTTPMiddleware does not propagate request.state in Starlette 1.0.0).

    Raises 401 if no user is set (auth middleware didn't run or was disabled).
    """
    user = getattr(request.state, "user", None)
    if user is not None:
        return user

    # Fallback: verify JWT directly (BaseHTTPMiddleware state propagation)
    auth_header = request.headers.get("Authorization", "")
    if not auth_header.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Not authenticated")

    svc = getattr(request.app.state, "auth_service", None)
    if svc is None:
        raise HTTPException(status_code=401, detail="Auth service not configured")

    token = auth_header[7:]
    try:
        return svc.verify_token(token)
    except ValueError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc


def require_role(required_role: Role) -> Callable:
    """Factory that returns a dependency enforcing a minimum role.

    When JWT auth is disabled (no auth_service on app.state),
    the role check is skipped — all users are treated as having full access.

    Role hierarchy: ADMIN > EDITOR > VIEWER.
    """
    _hierarchy = {Role.VIEWER: 0, Role.EDITOR: 1, Role.ADMIN: 2}

    def _check(request: Request) -> TokenPayload:
        svc = getattr(request.app.state, "auth_service", None)
        if svc is None:
            # API Key auth validated by ApiKeyMiddleware — treat as ADMIN.
            # BaseHTTPMiddleware doesn't propagate request.state, so we
            # trust that the middleware already rejected invalid keys.
            return TokenPayload(sub="api-key", role=Role.ADMIN, exp=0, iat=0)

        user = get_current_user(request)
        user_level = _hierarchy.get(user.role, -1)
        required_level = _hierarchy.get(required_role, -1)
        if user_level < required_level:
            raise HTTPException(
                status_code=403,
                detail=f"Insufficient permissions: requires {required_role.value}",
            )
        return user

    return _check
