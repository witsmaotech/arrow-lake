"""Security event logging helper — structlog (real-time) + turso audit_record (durable).

Per v1.9.2 plan §5 Review B4: the persistence call runs via ``asyncio.to_thread``
(synchronous ``Lake.audit_record`` off the event loop) rather than
``asyncio.create_task``. This deliberately avoids the fire-and-forget GC trap that
silently killed long kg_build tasks (see memory issue_kg_build_fire_forget_gc) —
an audit helper that gets GC'd mid-write would lose the very events it exists to
capture.

The helper is best-effort and NEVER raises: if ``lake`` is missing (e.g. tests
without app.state.lake) or ``audit_record`` fails, only the structlog line is
emitted so the protected request still succeeds.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

logger = logging.getLogger("arrow_lake.security")

# --- Event type vocabulary (shared literals; used across 7+ security paths) ---
LOGIN_SUCCESS = "security.login_success"
LOGIN_FAILURE = "security.login_failure"
LOGOUT = "security.logout"
USER_CREATED = "security.user_created"
ROLE_CHANGED = "security.role_changed"
USER_DEACTIVATED = "security.user_deactivated"
TOKEN_ISSUED = "security.token_issued"
TOKEN_REVOKED = "security.token_revoked"
ACL_GRANTED = "security.acl_granted"
ACL_REVOKED = "security.acl_revoked"
DENY_ADDED = "security.deny_added"
USER_UPDATED = "security.user_updated"
ACL_CHANGED = "security.acl_changed"  # schema-level ACL set/delete (v1.10.5 M2)
DENY_CHANGED = "security.deny_changed"  # deny removal (v1.10.5 M2)
PASSWORD_RESET_REQUESTED = "security.password_reset_requested"
PASSWORD_RESET = "security.password_reset"


def actor_of(user: Any) -> str:
    """Best-effort actor identity from a require_role/current-user object.

    ``require_role`` returns a dict; ``get_current_user`` returns an object with
    attributes. Handle both without raising.
    """
    if isinstance(user, dict):
        return str(user.get("username") or user.get("sub") or "admin")
    return str(getattr(user, "username", None) or getattr(user, "sub", None) or "unknown")


async def log_security_event(
    event_type: str,
    actor: str,
    *,
    lake: Any | None = None,
    dataset_name: str = "",
    detail: dict[str, Any] | None = None,
) -> None:
    """Emit a security event to structlog + the turso audit trail.

    Args:
        event_type: One of the ``security.*`` constants above.
        actor: Who triggered the event (username / sub).
        lake: The Lake facade (``app.state.lake``). ``None`` → log only.
        dataset_name: Affected dataset (for ACL/deny events).
        detail: Extra structured fields persisted in the audit payload.

    Never raises — audit persistence must not break the protected request.
    """
    payload: dict[str, Any] = {"actor": actor}
    if dataset_name:
        payload["dataset"] = dataset_name
    if detail:
        payload.update(detail)

    logger.info(
        "security_event type=%s actor=%s dataset=%s detail=%s",
        event_type, actor, dataset_name, payload,
    )
    if lake is None:
        return
    try:
        await asyncio.to_thread(
            lake.audit_record,
            event_type,
            dataset_name=dataset_name,
            actor=actor,
            payload=payload,
        )
    except Exception as exc:
        logger.warning("security_event audit_record failed: %s", str(exc)[:160])
