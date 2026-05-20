"""Dependency injection for the REST API.

Provides FastAPI Depends-callable factories for Lake instance, config,
and auth (current user, role-based access).
"""

from __future__ import annotations

from collections.abc import Callable
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


def get_checker(request: Request):
    """Return the PermissionChecker bound to this app.

    Falls back to a new instance when checker is not configured
    (e.g., in tests that don't set app.state.checker).
    """
    checker = getattr(request.app.state, "checker", None)
    if checker is not None:
        return checker
    from arrow_lake.api.rbac import PermissionChecker
    return PermissionChecker()


def get_current_user(request: Request) -> TokenPayload:
    """Return the authenticated user.

    Tries request.state.user first.  Falls back to verifying the Bearer
    token directly via the AuthService singleton.

    Raises 401 if no user is set (auth middleware didn't run or was disabled).
    """
    user = getattr(request.state, "user", None)
    if user is not None:
        return user

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

    When no auth_service is configured, access is denied by default (403)
    unless ``config.auth.allow_unauthenticated_access`` is explicitly True.

    Role hierarchy: ADMIN > EDITOR > VIEWER.
    """
    _hierarchy = {Role.VIEWER: 0, Role.EDITOR: 1, Role.ADMIN: 2}

    def _check(request: Request) -> TokenPayload:
        # Try to get user from middleware (API key or JWT set request.state.user)
        user = getattr(request.state, "user", None)

        if user is None:
            svc = getattr(request.app.state, "auth_service", None)
            if svc is not None:
                user = get_current_user(request)

        if user is None:
            cfg = getattr(request.app.state, "config", None)
            allow = getattr(cfg, "auth", None) and cfg.auth.allow_unauthenticated_access
            if not allow:
                raise HTTPException(
                    status_code=403,
                    detail="Authentication service not configured",
                )
            user = TokenPayload(sub="anonymous", role=Role.VIEWER, exp=0, iat=0)

        user_level = _hierarchy.get(user.role, -1)
        required_level = _hierarchy.get(required_role, -1)
        if user_level < required_level:
            raise HTTPException(
                status_code=403,
                detail=f"Insufficient permissions: requires {required_role.value}",
            )
        return user

    return _check
