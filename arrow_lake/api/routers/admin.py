"""Admin-only endpoints for system management."""

from __future__ import annotations

from fastapi import APIRouter, Depends

from arrow_lake.api.auth_models import Role
from arrow_lake.api.deps import require_role

router = APIRouter(prefix="/api/v2/admin", tags=["admin"])


@router.get("/users", summary="List users (admin only)")
async def list_users(
    _user: dict = Depends(require_role(Role.ADMIN)),
) -> dict:
    """List registered users. Currently returns placeholder."""
    # Placeholder — real user management requires database-backed sessions
    return {"users": [], "message": "User management not yet implemented"}
