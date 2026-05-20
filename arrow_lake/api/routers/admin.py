"""Admin-only endpoints for system management."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Path

from arrow_lake.api.auth_models import Role
from arrow_lake.api.deps import get_checker, require_role
from arrow_lake.api.models.common import _NAME_PATTERN
from arrow_lake.api.models.dataset import (
    AclDeleteResponse,
    AclEntry,
    AclListResponse,
    AclSetResponse,
    SetAclRequest,
)
from arrow_lake.api.rbac import DatasetACL

router = APIRouter(prefix="/api/v1/admin", tags=["admin"])


@router.get("/users", summary="List users (admin only)")
async def list_users(
    _user: dict = Depends(require_role(Role.ADMIN)),
) -> dict:
    """List registered users. Currently returns placeholder."""
    return {"users": [], "message": "User management not yet implemented"}


# ---------------------------------------------------------------------------
# Row/column ACL management
# ---------------------------------------------------------------------------


@router.put("/acl/{dataset}", response_model=AclSetResponse)
async def set_acl(
    dataset: str = Path(..., pattern=_NAME_PATTERN),
    *,
    req: SetAclRequest,
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
    _user: dict = Depends(require_role(Role.ADMIN)),
    checker=Depends(get_checker),
) -> AclDeleteResponse:
    """Delete row/column ACL for a role on a dataset (admin only)."""
    deleted = checker.delete_acl(dataset, role)
    return AclDeleteResponse(dataset=dataset, role=role, deleted=deleted)
