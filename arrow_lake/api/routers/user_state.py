"""Per-user state endpoints (v1.9.0 P3): saved queries, notifications,
preferences.

These endpoints are keyed by the authenticated user's id, which the v1.9.0
personal-token auth path sets on ``request.state.user_id``. Requests made
with the shared api_key (no real user) are rejected with 403 — user-state is
inherently per-user.

Backed by :class:`~arrow_lake.system_db.stores.user_state.UserStateStore`
(libSQL). When system_db is disabled the store is absent and endpoints return
a clear 503-style message.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Path, Query, Request
from pydantic import BaseModel, Field

from arrow_lake.api.deps import require_role
from arrow_lake.api.auth_models import Role

router = APIRouter(prefix="/api/v1/me", tags=["user-state"])


def _user_id(request: Request) -> int:
    """Resolve the authenticated user's id; 403 when auth has no real user."""
    uid = getattr(request.state, "user_id", None)
    if uid is None:
        raise HTTPException(
            status_code=403,
            detail="User-state endpoints require a personal API token (per-user auth).",
        )
    return int(uid)


def _store(request: Request) -> Any:
    store = getattr(request.app.state, "user_state_store", None)
    if store is None:
        raise HTTPException(
            status_code=503,
            detail="User-state requires system_db enabled.",
        )
    return store


# --------------------------------------------------------------------------- #
# Saved queries
# --------------------------------------------------------------------------- #
class SaveQueryRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=200)
    query_text: str
    query_type: str = Field("sql", pattern="^(sql|rag|search)$")
    dataset: str | None = None
    is_public: bool = False


class PreferencesRequest(BaseModel):
    preferences: dict[str, Any] = Field(default_factory=dict)


@router.post("/saved-queries", summary="Save a query for the current user")
async def save_query(
    body: SaveQueryRequest,
    request: Request,
    _u: dict = Depends(require_role(Role.VIEWER)),
) -> dict:
    store = _store(request)
    uid = _user_id(request)
    qid = store.save_query(
        uid, body.name, body.query_text,
        query_type=body.query_type, dataset=body.dataset, is_public=body.is_public,
    )
    return {"id": qid}


@router.get("/saved-queries", summary="List the current user's saved queries")
async def list_queries(
    request: Request,
    include_public: bool = Query(True),
    _u: dict = Depends(require_role(Role.VIEWER)),
) -> dict:
    store = _store(request)
    uid = _user_id(request)
    return {"queries": store.list_queries(uid, include_public=include_public)}


@router.delete("/saved-queries/{qid}", summary="Delete a saved query")
async def delete_query(
    request: Request,
    qid: int = Path(..., ge=1),
    _u: dict = Depends(require_role(Role.VIEWER)),
) -> dict:
    store = _store(request)
    uid = _user_id(request)
    return {"deleted": store.delete_query(uid, qid)}


# --------------------------------------------------------------------------- #
# Notifications
# --------------------------------------------------------------------------- #
@router.get("/notifications", summary="List the current user's notifications")
async def list_notifications(
    request: Request,
    unread_only: bool = Query(False),
    limit: int = Query(50, ge=1, le=200),
    _u: dict = Depends(require_role(Role.VIEWER)),
) -> dict:
    store = _store(request)
    uid = _user_id(request)
    return {
        "notifications": store.list_notifications(uid, unread_only=unread_only, limit=limit),
        "unread_count": store.unread_count(uid),
    }


@router.post("/notifications/read", summary="Mark notifications as read")
async def mark_notifications_read(
    request: Request,
    notification_id: int | None = None,
    _u: dict = Depends(require_role(Role.VIEWER)),
) -> dict:
    store = _store(request)
    uid = _user_id(request)
    return {"marked_read": store.mark_read(uid, notification_id)}


# --------------------------------------------------------------------------- #
# Preferences
# --------------------------------------------------------------------------- #
@router.get("/preferences", summary="Get the current user's preferences")
async def get_preferences(
    request: Request,
    _u: dict = Depends(require_role(Role.VIEWER)),
) -> dict:
    store = _store(request)
    uid = _user_id(request)
    return {"preferences": store.get_preferences(uid)}


@router.put("/preferences", summary="Set the current user's preferences")
async def set_preferences(
    body: PreferencesRequest,
    request: Request,
    _u: dict = Depends(require_role(Role.VIEWER)),
) -> dict:
    store = _store(request)
    uid = _user_id(request)
    store.set_preferences(uid, body.preferences)
    return {"preferences": store.get_preferences(uid)}
