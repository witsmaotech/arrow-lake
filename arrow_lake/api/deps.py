"""Dependency injection for the REST API.

Provides FastAPI Depends-callable factories for Lake instance, config,
and auth (current user, role-based access).
"""

from __future__ import annotations

from collections.abc import Callable
from functools import lru_cache

from fastapi import HTTPException, Request

from arrow_lake.api.auth_models import Role, TokenPayload
from arrow_lake.api.rbac import Permission
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


def authorize_dataset_read(name: str, request: Request) -> None:
    """Depends()-ready dataset read ACL (v1.10.7 WP1a).

    FastAPI injects the route's ``{name}`` path param into this sub-dependency,
    so every read endpoint can enforce dataset-level deny/ACL with a single
    ``Depends(authorize_dataset_read)`` instead of manual calls (which had
    coverage only in 3 routers — review H1).
    """
    authorize_dataset(request, name)


def _dead_letter_parent(name: str) -> str | None:
    """Return the parent dataset for a dead-letter table name, else None.

    Matches both the v1.10.7+ ``_{ds}_dead_letter`` spelling and the legacy
    ``{ds}_dead_letter`` one (pre-rename tables stay guarded).
    """
    from arrow_lake._system_tables import DEAD_LETTER_SUFFIX

    if not name.endswith(DEAD_LETTER_SUFFIX):
        return None
    stem = name[: -len(DEAD_LETTER_SUFFIX)]
    if stem.startswith("_"):
        stem = stem[1:]
    return stem or None


def authorize_dataset(request: Request, name: str, *, write: bool = False) -> None:
    """Enforce dataset-level ACL on top of role-level require_role (v1.9.1 security).

    Raises 403 if the user's role lacks read (always) or write (when write=True)
    access to ``name``. Note: actual row/column filter application at the
    lake.read_dataset layer (passing the user through) is a separate follow-up.

    Dead-letter tables (v1.10.7 review B-3) are ADMIN-only and inherit the
    parent dataset's deny/ACL: the rows a quality gate rejects are exactly the
    rows the dataset's policy refused — often the most sensitive ones.
    """
    user = getattr(request.state, "user", None) or get_current_user(request)
    parent = _dead_letter_parent(name)
    if parent is not None:
        if user.role != Role.ADMIN:
            raise HTTPException(
                status_code=403,
                detail=f"Dead-letter table '{name}' is admin-only",
            )
        return  # ADMIN bypasses dataset ACL (consistent with check_dataset_access)
    checker = get_checker(request)
    if not checker.check_dataset_access(role=user.role, dataset=name, action="read"):
        raise HTTPException(status_code=403, detail=f"No read access to dataset '{name}'")
    if write and not checker.check_dataset_access(role=user.role, dataset=name, action="write"):
        raise HTTPException(status_code=403, detail=f"No write access to dataset '{name}'")


def _resolve_user(request: Request) -> TokenPayload | None:
    """Return the authenticated user from middleware state or Bearer fallback."""
    user = getattr(request.state, "user", None)
    if user is None:
        svc = getattr(request.app.state, "auth_service", None)
        if svc is not None:
            user = get_current_user(request)
    return user


def _attach_user_id(request: Request, user: TokenPayload) -> None:
    """Expose the numeric user id on the payload for task notifications (v1.9.3)."""
    uid = getattr(request.state, "user_id", None)
    if uid is None and getattr(user, "sub", "").isdigit():
        uid = int(user.sub)  # v1.9.3: JWT path carries user_id in sub (login: str(user["id"]))
    if uid is not None and getattr(user, "user_id", None) is None:
        try:
            user.user_id = uid
        except Exception:  # noqa: BLE001
            pass


_ROLE_HIERARCHY = {Role.VIEWER: 0, Role.EDITOR: 1, Role.ADMIN: 2}


def require_role(required_role: Role) -> Callable:
    """Factory that returns a dependency enforcing a minimum role.

    When no auth_service is configured, access is denied by default (403)
    unless ``config.auth.allow_unauthenticated_access`` is explicitly True.

    Role hierarchy: ADMIN > EDITOR > VIEWER.
    """
    _hierarchy = _ROLE_HIERARCHY

    def _check(request: Request) -> TokenPayload:
        # Try to get user from middleware (API key or JWT set request.state.user)
        user = _resolve_user(request)

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
        _attach_user_id(request, user)
        return user

    return _check


# Minimum role gating each permission when the token carries NO permissions
# claim (pre-v1.10.5 JWTs, the shared api-key identity, scope-less personal
# tokens). Matches the require_role levels these endpoints enforced before
# v1.10.5 M4 — the fallback must be behaviour-preserving.
_PERMISSION_FALLBACK_ROLE: dict[Permission, Role] = {
    Permission.DATASET_READ: Role.VIEWER,
    Permission.DATASET_WRITE: Role.EDITOR,
    Permission.DATASET_DELETE: Role.EDITOR,
    Permission.ADMIN_MANAGE: Role.ADMIN,
}


def require_permission(permission: Permission) -> Callable:
    """Factory enforcing a specific permission — scope-aware (v1.10.5 M4).

    * Token with a NON-EMPTY ``permissions`` claim → the claim IS the
      authorization: exact membership check, no role floor (least privilege,
      in both directions — scopes can restrict below the role and, for
      admin-issued personal tokens, grant one specific action above it).
    * Token with an EMPTY claim → role-hierarchy fallback at the level the
      endpoint enforced pre-M4 (pre-v1.10.5 JWTs, shared api-key identity,
      scope-less personal tokens all behave exactly as before).

    This is the seam a future external IdP maps its scopes onto; dataset ACL
    (``authorize_dataset`` / row-column filters) is untouched — that is the
    data layer.
    """
    role_check = require_role(_PERMISSION_FALLBACK_ROLE[permission])

    def _check(request: Request) -> TokenPayload:
        user = _resolve_user(request)
        if user is not None and user.permissions:
            if permission.value not in user.permissions:
                raise HTTPException(
                    status_code=403,
                    detail=f"Missing permission: {permission.value}",
                )
            _attach_user_id(request, user)
            return user
        return role_check(request)  # empty claim / unauthenticated → hierarchy

    return _check
