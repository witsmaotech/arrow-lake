"""Admin-only endpoints for system management."""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, Path, Request
from pydantic import BaseModel, Field

from arrow_lake.api.auth_models import Role
from arrow_lake.api.deps import get_checker, require_role
from arrow_lake.api._security_log import (
    ACL_GRANTED,
    ACL_REVOKED,
    DENY_ADDED,
    PASSWORD_RESET_REQUESTED,
    ROLE_CHANGED,
    TOKEN_ISSUED,
    TOKEN_REVOKED,
    USER_CREATED,
    USER_DEACTIVATED,
    actor_of,
    log_security_event,
)
from arrow_lake.api.models.common import _NAME_PATTERN
from arrow_lake.api.models.dataset import (
    AclDeleteResponse,
    AclEntry,
    AclListResponse,
    AclSetResponse,
    SetAclRequest,
)
from arrow_lake.api.rbac import DatasetACL, SchemaACL

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/admin", tags=["admin"])


@router.get("/users", summary="List users (admin only)")
async def list_users(
    request: Request,
    _user: dict = Depends(require_role(Role.ADMIN)),
) -> dict:
    """List registered users (v1.9.0: backed by the libSQL identity store)."""
    store = getattr(request.app.state, "identity_store", None)
    if store is None:
        return {"users": [], "message": "User management requires system_db enabled"}
    try:
        users = store.list_users()
    except Exception:  # noqa: BLE001 — fail-soft
        return {"users": [], "message": "User store unavailable"}
    return {"users": users, "count": len(users)}


class CreateUserRequest(BaseModel):
    username: str = Field(..., pattern=_NAME_PATTERN)
    email: str | None = None
    role: str = Field("viewer", pattern=r"^(admin|editor|viewer)$")
    password: str = Field(..., min_length=8)


@router.post("/users", summary="Create user (admin only)")
async def create_user(
    request: Request,
    *,
    req: CreateUserRequest,
    _user: dict = Depends(require_role(Role.ADMIN)),
) -> dict:
    """Create a user with a password (v1.9.1: pbkdf2-hashed, stored in libSQL)."""
    store = getattr(request.app.state, "identity_store", None)
    if store is None:
        raise HTTPException(status_code=503, detail="User management requires system_db enabled")
    from arrow_lake.api.passwords import hash_password

    try:
        uid = store.create_user(
            req.username,
            email=req.email,
            role=req.role,
            password_hash=hash_password(req.password),
        )
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=409, detail="Could not create user (conflict or invalid input)") from exc
    logger.info("user_created id=%s username=%s role=%s actor=%s", uid, req.username, req.role, getattr(_user, "sub", "?"))
    await log_security_event(
        USER_CREATED, actor_of(_user),
        lake=getattr(request.app.state, "lake", None),
        detail={"user_id": uid, "username": req.username, "role": req.role},
    )
    return {"id": uid, "username": req.username, "email": req.email, "role": req.role}


# ---------------------------------------------------------------------------
# Roles catalog + user update/deactivate (v1.9.1)
# ---------------------------------------------------------------------------
@router.get("/roles", summary="List roles + permissions (admin only)")
async def list_roles(
    _user: dict = Depends(require_role(Role.ADMIN)),
) -> dict:
    """Static role catalog (admin > editor > viewer) + permission matrix."""
    return {
        "roles": [
            {"name": "admin", "level": 2, "permissions": ["dataset:read", "dataset:write", "dataset:delete", "admin:manage"]},
            {"name": "editor", "level": 1, "permissions": ["dataset:read", "dataset:write", "dataset:delete"]},
            {"name": "viewer", "level": 0, "permissions": ["dataset:read"]},
        ]
    }


class UpdateUserRequest(BaseModel):
    email: str | None = None
    role: str | None = Field(None, pattern=r"^(admin|editor|viewer)$")
    password: str | None = Field(None, min_length=8)
    is_active: bool | None = None


@router.put("/users/{user_id}", summary="Update user fields (admin only)")
async def update_user(
    user_id: int = Path(..., ge=1),
    *,
    req: UpdateUserRequest,
    request: Request,
    _user: dict = Depends(require_role(Role.ADMIN)),
) -> dict:
    """Patch-selectable user fields (email/role/password/is_active)."""
    store = getattr(request.app.state, "identity_store", None)
    if store is None:
        raise HTTPException(status_code=503, detail="User management requires system_db enabled")
    password_hash = None
    if req.password is not None:
        from arrow_lake.api.passwords import hash_password

        password_hash = hash_password(req.password)
    if password_hash is None and req.email is None and req.role is None and req.is_active is None:
        raise HTTPException(status_code=422, detail="No fields to update")
    try:
        ok = store.update_user(
            user_id,
            email=req.email,
            role=req.role,
            password_hash=password_hash,
            is_active=req.is_active,
        )
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=409, detail="Could not update user (conflict or invalid input)") from exc
    if not ok:
        raise HTTPException(status_code=404, detail="User not found")
    # v1.10.5 M0: privilege-affecting changes kill the user's existing JWTs
    # (role embedded in token / password rotated / account reactivated).
    if password_hash is not None or req.role is not None or req.is_active is not None:
        store.bump_token_valid_after(user_id)
    if req.role is not None:
        await log_security_event(
            ROLE_CHANGED, actor_of(_user),
            lake=getattr(request.app.state, "lake", None),
            detail={"target_user_id": user_id, "new_role": req.role},
        )
    return {"id": user_id, "updated": True}


@router.delete("/users/{user_id}", summary="Deactivate user (soft delete, admin only)")
async def deactivate_user(
    user_id: int = Path(..., ge=1),
    *,
    request: Request,
    _user: dict = Depends(require_role(Role.ADMIN)),
) -> dict:
    """Soft-delete: set is_active=False. Tokens auto-invalidate (inactive user blocks auth)."""
    store = getattr(request.app.state, "identity_store", None)
    if store is None:
        raise HTTPException(status_code=503, detail="User management requires system_db enabled")
    store.set_user_active(user_id, False)
    # v1.10.5 M0: cut off the user's outstanding JWTs immediately.
    store.bump_token_valid_after(user_id)
    logger.info("user_deactivated id=%s actor=%s", user_id, getattr(_user, "sub", "?"))
    await log_security_event(
        USER_DEACTIVATED, actor_of(_user),
        lake=getattr(request.app.state, "lake", None),
        detail={"target_user_id": user_id},
    )
    return {"id": user_id, "deactivated": True}


# ---------------------------------------------------------------------------
# One-time password reset (v1.10.5 M1) — no email channel: the plaintext token
# is returned to the admin exactly once and relayed to the user out of band.
# ---------------------------------------------------------------------------
@router.post(
    "/users/{user_id}/password-reset",
    summary="Issue a one-time password reset token (admin only)",
)
async def issue_password_reset(
    user_id: int = Path(..., ge=1),
    *,
    request: Request,
    _user: dict = Depends(require_role(Role.ADMIN)),
) -> dict:
    """Generate a single-use reset token (30min TTL). Plaintext returned EXACTLY ONCE."""
    store = getattr(request.app.state, "identity_store", None)
    if store is None:
        raise HTTPException(status_code=503, detail="User management requires system_db enabled")
    target = store.get_user(user_id)
    if target is None:
        raise HTTPException(status_code=404, detail="User not found")
    if not target.get("is_active", False):
        raise HTTPException(status_code=422, detail="Cannot reset password for a deactivated user")
    plaintext, rec = store.create_password_reset_token(user_id)
    logger.info(
        "password_reset_issued user_id=%s username=%s actor=%s",
        user_id, target["username"], getattr(_user, "sub", "?"),
    )
    await log_security_event(
        PASSWORD_RESET_REQUESTED, actor_of(_user),
        lake=getattr(request.app.state, "lake", None),
        detail={"target_user_id": user_id, "username": target["username"]},
    )
    return {
        "token": plaintext,
        "user_id": user_id,
        "username": target["username"],
        "expires_at": rec["expires_at"],
    }


# ---------------------------------------------------------------------------
# Personal tokens (admin manages on behalf of a user) — v1.9.1
# ---------------------------------------------------------------------------
class CreateTokenRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=120)
    scopes: list[str] = Field(default_factory=list)
    expires_at: str | None = None


@router.post("/users/{user_id}/tokens", summary="Issue a personal token (admin only)")
async def issue_token(
    user_id: int = Path(..., ge=1),
    *,
    req: CreateTokenRequest,
    request: Request,
    _user: dict = Depends(require_role(Role.ADMIN)),
) -> dict:
    """Issue a personal API token. The plaintext token is returned EXACTLY ONCE."""
    store = getattr(request.app.state, "identity_store", None)
    if store is None:
        raise HTTPException(status_code=503, detail="User management requires system_db enabled")
    try:
        plaintext, rec = store.create_token(
            user_id, name=req.name, scopes=req.scopes or None, expires_at=req.expires_at,
        )
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=409, detail="Could not issue token (conflict or invalid input)") from exc
    logger.info("personal_token_issued user_id=%s name=%s actor=%s", user_id, req.name, getattr(_user, "sub", "?"))
    await log_security_event(
        TOKEN_ISSUED, actor_of(_user),
        lake=getattr(request.app.state, "lake", None),
        detail={"target_user_id": user_id, "token_name": req.name},
    )
    return {
        "token": plaintext,
        "id": rec["id"],
        "name": rec["name"],
        "token_prefix": rec["token_prefix"],
        "scopes": rec["scopes"],
        "expires_at": rec["expires_at"],
    }


@router.get("/users/{user_id}/tokens", summary="List a user's tokens (admin only)")
async def list_user_tokens(
    user_id: int = Path(..., ge=1),
    *,
    request: Request,
    _user: dict = Depends(require_role(Role.ADMIN)),
) -> dict:
    store = getattr(request.app.state, "identity_store", None)
    if store is None:
        raise HTTPException(status_code=503, detail="User management requires system_db enabled")
    return {"tokens": store.list_tokens(user_id)}


@router.delete("/users/{user_id}/tokens/{token_id}", summary="Revoke a token (admin only)")
async def revoke_user_token(
    user_id: int = Path(..., ge=1),
    token_id: int = Path(..., ge=1),
    *,
    request: Request,
    _user: dict = Depends(require_role(Role.ADMIN)),
) -> dict:
    store = getattr(request.app.state, "identity_store", None)
    if store is None:
        raise HTTPException(status_code=503, detail="User management requires system_db enabled")
    revoked = store.revoke_token(token_id)
    await log_security_event(
        TOKEN_REVOKED, actor_of(_user),
        lake=getattr(request.app.state, "lake", None),
        detail={"target_user_id": user_id, "token_id": token_id},
    )
    return {"id": token_id, "revoked": revoked}


# ---------------------------------------------------------------------------
# Row/column ACL management
# ---------------------------------------------------------------------------


@router.put("/acl/{dataset}", response_model=AclSetResponse)
async def set_acl(
    dataset: str = Path(..., pattern=_NAME_PATTERN),
    *,
    req: SetAclRequest,
    request: Request,
    _user: dict = Depends(require_role(Role.ADMIN)),
    checker=Depends(get_checker),
) -> AclSetResponse:
    """Set row/column ACL for a role on a dataset (admin only)."""
    acl = DatasetACL(
        dataset=dataset,
        role=req.role,
        visible_columns=frozenset(req.visible_columns),
        row_filter=req.row_filter,
    )
    checker.set_acl(acl)
    await log_security_event(
        ACL_GRANTED, actor_of(_user),
        lake=getattr(request.app.state, "lake", None),
        dataset_name=dataset,
        detail={"role": req.role},
    )
    return AclSetResponse(dataset=dataset, role=req.role)


@router.get("/acl/{dataset}", response_model=AclListResponse)
async def list_acls(
    dataset: str = Path(..., pattern=_NAME_PATTERN),
    *,
    _user: dict = Depends(require_role(Role.ADMIN)),
    checker=Depends(get_checker),
) -> AclListResponse:
    """List all row/column ACLs for a dataset (admin only)."""
    acls = checker.list_acls(dataset)
    return AclListResponse(
        dataset=dataset,
        acls=[
            AclEntry(
                role=a.role,
                visible_columns=sorted(a.visible_columns),
                row_filter=a.row_filter,
            )
            for a in acls
        ],
    )


@router.delete("/acl/{dataset}/{role}", response_model=AclDeleteResponse)
async def delete_acl(
    dataset: str = Path(..., pattern=_NAME_PATTERN),
    role: str = Path(..., pattern=r"^(viewer|editor)$"),
    *,
    request: Request,
    _user: dict = Depends(require_role(Role.ADMIN)),
    checker=Depends(get_checker),
) -> AclDeleteResponse:
    """Delete row/column ACL for a role on a dataset (admin only)."""
    deleted = checker.delete_acl(dataset, role)
    await log_security_event(
        ACL_REVOKED, actor_of(_user),
        lake=getattr(request.app.state, "lake", None),
        dataset_name=dataset,
        detail={"role": role, "deleted": deleted},
    )
    return AclDeleteResponse(dataset=dataset, role=role, deleted=deleted)


# ---------------------------------------------------------------------------
# Schema-level ACL management (v1.5.1)
# ---------------------------------------------------------------------------


class SchemaAclRequest(BaseModel):
    role: str = Field(..., pattern=r"^(viewer|editor)$")
    allowed_actions: list[str] = Field(default_factory=list)
    denied_actions: list[str] = Field(default_factory=list)


class SchemaAclResponse(BaseModel):
    schema_name: str
    role: str
    allowed_actions: list[str]
    denied_actions: list[str]

    model_config = {"populate_by_name": True}


class SchemaAclListResponse(BaseModel):
    schema_name: str
    acls: list[SchemaAclResponse]


class DenyRequest(BaseModel):
    action: str = Field(..., min_length=1)


class DenyResponse(BaseModel):
    dataset: str
    action: str
    denied: bool


class DenyListResponse(BaseModel):
    dataset: str
    denied_actions: list[str]


@router.put("/acl/schema/{schema_name}", response_model=SchemaAclResponse)
async def set_schema_acl(
    schema_name: str = Path(..., pattern=_NAME_PATTERN),
    *,
    req: SchemaAclRequest,
    _user: dict = Depends(require_role(Role.ADMIN)),
    checker=Depends(get_checker),
) -> SchemaAclResponse:
    """Set schema-level ACL inherited by all child datasets (admin only)."""
    acl = SchemaACL(
        schema=schema_name,
        role=req.role,
        allowed_actions=frozenset(req.allowed_actions),
        denied_actions=frozenset(req.denied_actions),
    )
    checker.set_schema_acl(acl)
    return SchemaAclResponse(
        schema_name=schema_name,
        role=req.role,
        allowed_actions=sorted(req.allowed_actions),
        denied_actions=sorted(req.denied_actions),
    )


@router.get("/acl/schema/{schema_name}", response_model=SchemaAclListResponse)
async def list_schema_acls(
    schema_name: str = Path(..., pattern=_NAME_PATTERN),
    *,
    _user: dict = Depends(require_role(Role.ADMIN)),
    checker=Depends(get_checker),
) -> SchemaAclListResponse:
    """List all schema-level ACLs for a schema (admin only)."""
    acls = checker.list_schema_acls(schema_name)
    return SchemaAclListResponse(
        schema_name=schema_name,
        acls=[
            SchemaAclResponse(
                schema_name=a.schema,
                role=a.role,
                allowed_actions=sorted(a.allowed_actions),
                denied_actions=sorted(a.denied_actions),
            )
            for a in acls
        ],
    )


@router.delete("/acl/schema/{schema_name}/{role}", response_model=SchemaAclResponse)
async def delete_schema_acl(
    schema_name: str = Path(..., pattern=_NAME_PATTERN),
    role: str = Path(..., pattern=r"^(viewer|editor)$"),
    *,
    _user: dict = Depends(require_role(Role.ADMIN)),
    checker=Depends(get_checker),
) -> SchemaAclResponse:
    """Delete schema-level ACL for a role (admin only)."""
    checker.delete_schema_acl(schema_name, role)
    return SchemaAclResponse(
        schema_name=schema_name, role=role, allowed_actions=[], denied_actions=[],
    )


@router.put("/deny/{dataset}", response_model=DenyResponse)
async def deny_action(
    dataset: str = Path(..., pattern=_NAME_PATTERN),
    *,
    req: DenyRequest,
    request: Request,
    _user: dict = Depends(require_role(Role.ADMIN)),
    checker=Depends(get_checker),
) -> DenyResponse:
    """Add explicit Deny for an action on a dataset (admin only)."""
    checker.deny_action(dataset, req.action)
    await log_security_event(
        DENY_ADDED, actor_of(_user),
        lake=getattr(request.app.state, "lake", None),
        dataset_name=dataset,
        detail={"action": req.action},
    )
    return DenyResponse(dataset=dataset, action=req.action, denied=True)


@router.delete("/deny/{dataset}/{action}", response_model=DenyResponse)
async def remove_deny(
    dataset: str = Path(..., pattern=_NAME_PATTERN),
    action: str = Path(..., min_length=1),
    *,
    _user: dict = Depends(require_role(Role.ADMIN)),
    checker=Depends(get_checker),
) -> DenyResponse:
    """Remove explicit Deny for an action on a dataset (admin only)."""
    removed = checker.remove_deny(dataset, action)
    return DenyResponse(dataset=dataset, action=action, denied=not removed)


@router.get("/deny/{dataset}", response_model=DenyListResponse)
async def list_denies(
    dataset: str = Path(..., pattern=_NAME_PATTERN),
    *,
    _user: dict = Depends(require_role(Role.ADMIN)),
    checker=Depends(get_checker),
) -> DenyListResponse:
    """List all denied actions for a dataset (admin only)."""
    return DenyListResponse(dataset=dataset, denied_actions=sorted(checker.list_denies(dataset)))
