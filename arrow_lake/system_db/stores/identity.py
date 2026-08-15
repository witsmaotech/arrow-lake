"""IdentityStore — users + self-managed personal API tokens.

Replaces the single shared ``api_key`` + hardcoded ``user_id="api-user"``
with a real ``users`` table and per-user, revocable, expiring tokens.

Token safety (per v1.9.0 plan §5.6):

* generated with ``secrets.token_urlsafe(32)``, prefixed ``al_``
* stored only as ``sha256(token)`` — plaintext returned exactly once at creation
* validated by hashing the presented token and looking up the hash, then a
  constant-time :func:`hmac.compare_digest` confirmation (defense-in-depth)
* ``last_used_at`` updated on successful validation
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
from typing import Any

import structlog

from arrow_lake.system_db.connection import SystemDB, SystemDBError
from arrow_lake.system_db.stores.base import FailMode

logger = structlog.get_logger(__name__)

TOKEN_PREFIX = "al_"  # arrow-lake personal token
RESET_TOKEN_PREFIX = "alr_"  # arrow-lake one-time password reset token (v1.10.5 M1)

# Default lifetime of a password-reset token (admin hands it to the user out of
# band, so the window only needs to cover that handoff).
RESET_TOKEN_TTL_SECONDS = 1800  # 30 min
# Consumed/expired reset-token rows are purged once older than this (days).
_RESET_TOKEN_RETENTION_DAYS = 7

# Throttle window for last_used_at updates: writing it on every authenticated
# request would serialize all API calls through the single-writer DB. Only
# refresh when staler than this (the value is read in the same SELECT, so the
# check is free).
LAST_USED_THROTTLE_SECONDS = 60


def _should_update_last_used(last_used_at: str | None) -> bool:
    """True when last_used_at is missing or staler than the throttle window."""
    if not last_used_at:
        return True
    try:
        from datetime import datetime, timezone

        last = datetime.fromisoformat(last_used_at)
        if last.tzinfo is None:
            last = last.replace(tzinfo=timezone.utc)
        return (datetime.now(timezone.utc) - last).total_seconds() > LAST_USED_THROTTLE_SECONDS
    except (ValueError, TypeError):
        return True


def _hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _prefix(token: str) -> str:
    return token[:8]


class IdentityStore:
    """Users + personal_tokens persistence.

    Security-sensitive: uses ``FailMode.FAIL_CLOSE``.
    """

    fail_mode = FailMode.FAIL_CLOSE

    def __init__(self, db: SystemDB) -> None:
        self._db = db

    # ------------------------------------------------------------------
    # users
    # ------------------------------------------------------------------
    def create_user(
        self,
        username: str,
        *,
        email: str | None = None,
        role: str = "viewer",
        password_hash: str | None = None,
    ) -> int:
        with self._db.with_write() as db:
            cur = db.execute(
                "INSERT INTO users (username, email, role, password_hash) "
                "VALUES (?, ?, ?, ?)",
                (username, email, role, password_hash),
            )
            user_id = cur.lastrowid if cur is not None else None
        if user_id is None:
            # username collision → resolve existing id
            existing = self.get_user_by_username(username)
            if existing is None:
                raise SystemDBError(f"could not create or resolve user {username!r}")
            return int(existing["id"])
        return int(user_id)

    def get_user(self, user_id: int) -> dict[str, Any] | None:
        return self._one(
            "SELECT id, username, email, role, is_active, created_at, updated_at "
            "FROM users WHERE id = ?",
            (user_id,),
        )

    def get_user_by_username(self, username: str) -> dict[str, Any] | None:
        return self._one(
            "SELECT id, username, email, role, is_active, created_at, updated_at "
            "FROM users WHERE username = ?",
            (username,),
        )

    def get_user_with_credentials(self, username: str) -> dict[str, Any] | None:
        """Return user row including password_hash (for login verification)."""
        cur = self._db.execute(
            "SELECT id, username, email, role, is_active, password_hash "
            "FROM users WHERE username = ?",
            (username,),
        )
        row = cur.fetchone() if cur is not None else None
        if row is None:
            return None
        return {
            "id": row[0],
            "username": row[1],
            "email": row[2],
            "role": row[3],
            "is_active": bool(row[4]),
            "password_hash": row[5],
        }

    def list_users(self) -> list[dict[str, Any]]:
        cur = self._db.execute(
            "SELECT id, username, email, role, is_active, created_at, updated_at "
            "FROM users ORDER BY id"
        )
        rows = cur.fetchall() if cur is not None else []
        return [self._row_to_user(r) for r in rows]

    def set_user_active(self, user_id: int, is_active: bool) -> None:
        with self._db.with_write() as db:
            db.execute(
                "UPDATE users SET is_active = ?, updated_at = datetime('now') "
                "WHERE id = ?",
                (1 if is_active else 0, user_id),
            )

    # ------------------------------------------------------------------
    # per-user token cutoff (v1.10.5 M0)
    # ------------------------------------------------------------------
    def get_token_valid_after(self, user_id: int) -> float | None:
        """Return the epoch-seconds token cutoff for a user, or None."""
        cur = self._db.execute(
            "SELECT token_valid_after FROM users WHERE id = ?", (user_id,)
        )
        row = cur.fetchone() if cur is not None else None
        if row is None or row[0] is None:
            return None
        return float(row[0])

    def bump_token_valid_after(self, user_id: int) -> bool:
        """Advance the cutoff to now, invalidating all of the user's JWTs.

        Called on password/role change and deactivation so existing tokens
        die on their next request instead of at natural expiry.
        Returns True if a row was updated.
        """
        import time as _time

        with self._db.with_write() as db:
            cur = db.execute(
                "UPDATE users SET token_valid_after = ?, updated_at = datetime('now') "
                "WHERE id = ?",
                (_time.time(), user_id),
            )
            return cur is not None and cur.rowcount > 0

    def update_user(
        self,
        user_id: int,
        *,
        email: str | None = None,
        role: str | None = None,
        password_hash: str | None = None,
        is_active: bool | None = None,
    ) -> bool:
        """Patch-selectable user fields. Returns True if a row was updated.

        Only fields explicitly passed are written; updated_at is refreshed.
        """
        fields: list[str] = []
        params: list[Any] = []
        if email is not None:
            fields.append("email = ?")
            params.append(email)
        if role is not None:
            fields.append("role = ?")
            params.append(role)
        if password_hash is not None:
            fields.append("password_hash = ?")
            params.append(password_hash)
        if is_active is not None:
            fields.append("is_active = ?")
            params.append(1 if is_active else 0)
        if not fields:
            return False  # nothing to update
        fields.append("updated_at = datetime('now')")
        params.append(user_id)
        with self._db.with_write() as db:
            cur = db.execute(
                f"UPDATE users SET {', '.join(fields)} WHERE id = ?",
                tuple(params),
            )
            return cur is not None and cur.rowcount > 0

    # ------------------------------------------------------------------
    # personal tokens
    # ------------------------------------------------------------------
    def create_token(
        self,
        user_id: int,
        *,
        name: str,
        scopes: list[str] | None = None,
        expires_at: str | None = None,
    ) -> tuple[str, dict[str, Any]]:
        """Create a token. Returns ``(plaintext_token, record)``.

        The plaintext is returned exactly once and never stored.
        """
        import json

        plaintext = TOKEN_PREFIX + secrets.token_urlsafe(32)
        token_hash = _hash_token(plaintext)
        scopes_json = json.dumps(scopes) if scopes else None
        with self._db.with_write() as db:
            cur = db.execute(
                "INSERT INTO personal_tokens "
                "(user_id, name, token_hash, token_prefix, scopes, expires_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (user_id, name, token_hash, _prefix(plaintext), scopes_json, expires_at),
            )
            token_id = cur.lastrowid if cur is not None else None
        record = {
            "id": token_id,
            "user_id": user_id,
            "name": name,
            "token_prefix": _prefix(plaintext),
            "scopes": scopes,
            "expires_at": expires_at,
        }
        return plaintext, record

    def validate_token(self, plaintext: str) -> dict[str, Any] | None:
        """Resolve a presented token to its user + scopes, or None.

        None when: token malformed, unknown, revoked, expired, or user inactive.
        """
        if not plaintext.startswith(TOKEN_PREFIX):
            return None
        token_hash = _hash_token(plaintext)
        cur = self._db.execute(
            "SELECT pt.id, pt.user_id, pt.token_hash, pt.scopes, pt.expires_at, "
            "pt.revoked_at, pt.last_used_at, u.username, u.role, u.is_active "
            "FROM personal_tokens pt JOIN users u ON u.id = pt.user_id "
            "WHERE pt.token_hash = ?",
            (token_hash,),
        )
        row = cur.fetchone() if cur is not None else None
        if row is None:
            return None
        (tid, uid, stored_hash, scopes_json, expires_at, revoked_at,
         last_used_at, username, role, is_active) = row
        # constant-time confirmation (defense-in-depth over the index lookup)
        if not hmac.compare_digest(stored_hash, token_hash):
            return None
        if revoked_at or not is_active:
            return None
        if expires_at:
            # ISO8601 lexical compare is valid for 'Z'/UTC timestamps we issue.
            from datetime import datetime, timezone

            try:
                exp = datetime.fromisoformat(expires_at.replace("Z", "+00:00"))
                if datetime.now(timezone.utc) > exp:
                    return None
            except ValueError:
                logger.warning("identity_token_bad_expiry", expires_at=expires_at)

        # Throttled last_used_at update: only write when staler than the window
        # so high-frequency authenticated requests don't all hit the writer.
        if _should_update_last_used(last_used_at):
            try:
                with self._db.with_write() as db:
                    db.execute(
                        "UPDATE personal_tokens SET last_used_at = datetime('now') "
                        "WHERE id = ?",
                        (tid,),
                    )
            except SystemDBError:
                pass

        import json

        return {
            "token_id": tid,
            "user_id": uid,
            "username": username,
            "role": role,
            "scopes": _loads(scopes_json),
        }

    def revoke_token(self, token_id: int) -> bool:
        with self._db.with_write() as db:
            cur = db.execute(
                "UPDATE personal_tokens SET revoked_at = datetime('now') "
                "WHERE id = ? AND revoked_at IS NULL",
                (token_id,),
            )
            return cur is not None and cur.rowcount > 0

    def revoke_all_tokens(self, user_id: int) -> int:
        """Revoke every active personal token for a user. Returns count revoked.

        Used by the password-reset flow (v1.10.5 M1 review): a password reset is
        incident response, so outstanding machine credentials must die with the
        compromised password, not just the JWTs.
        """
        with self._db.with_write() as db:
            cur = db.execute(
                "UPDATE personal_tokens SET revoked_at = datetime('now') "
                "WHERE user_id = ? AND revoked_at IS NULL",
                (user_id,),
            )
            return cur.rowcount if cur is not None else 0

    def list_tokens(self, user_id: int) -> list[dict[str, Any]]:
        cur = self._db.execute(
            "SELECT id, name, token_prefix, scopes, expires_at, last_used_at, "
            "created_at, revoked_at FROM personal_tokens WHERE user_id = ? "
            "ORDER BY id",
            (user_id,),
        )
        rows = cur.fetchall() if cur is not None else []
        return [
            {
                "id": r[0],
                "name": r[1],
                "token_prefix": r[2],
                "scopes": _loads(r[3]),
                "expires_at": r[4],
                "last_used_at": r[5],
                "created_at": r[6],
                "revoked_at": r[7],
            }
            for r in rows
        ]

    # ------------------------------------------------------------------
    # one-time password reset tokens (v1.10.5 M1)
    # ------------------------------------------------------------------
    def create_password_reset_token(
        self, user_id: int, *, ttl_seconds: int = RESET_TOKEN_TTL_SECONDS
    ) -> tuple[str, dict[str, Any]]:
        """Issue a single-use reset token. Returns ``(plaintext, record)``.

        The plaintext is returned exactly once and never stored (sha256 only);
        the admin relays it to the user out of band.
        """
        from datetime import datetime, timedelta, timezone

        plaintext = RESET_TOKEN_PREFIX + secrets.token_urlsafe(32)
        expires_at = (
            datetime.now(timezone.utc) + timedelta(seconds=ttl_seconds)
        ).strftime("%Y-%m-%dT%H:%M:%SZ")
        with self._db.with_write() as db:
            # Single outstanding token per user: issuing a new one burns any
            # prior unconsumed siblings (review: no second token waiting to
            # re-rotate the password within its own TTL).
            db.execute(
                "UPDATE password_reset_tokens SET used_at = datetime('now') "
                "WHERE user_id = ? AND used_at IS NULL",
                (user_id,),
            )
            # Piggyback purge of rows older than 7 days — bounded table growth
            # without a separate sweeper (issuance is admin-only, low volume).
            db.execute(
                "DELETE FROM password_reset_tokens WHERE expires_at < ?",
                (
                    (
                        datetime.now(timezone.utc) - timedelta(days=_RESET_TOKEN_RETENTION_DAYS)
                    ).strftime("%Y-%m-%dT%H:%M:%SZ"),
                ),
            )
            cur = db.execute(
                "INSERT INTO password_reset_tokens (user_id, token_hash, expires_at) "
                "VALUES (?, ?, ?)",
                (user_id, _hash_token(plaintext), expires_at),
            )
            token_id = cur.lastrowid if cur is not None else None
        record = {"id": token_id, "user_id": user_id, "expires_at": expires_at}
        return plaintext, record

    def consume_password_reset_token(self, plaintext: str) -> int | None:
        """Validate and burn a reset token. Returns the ``user_id``, or None.

        None when: malformed, unknown, already used, expired. The burn is a
        conditional UPDATE inside the write transaction, so a concurrent
        double-spend sees rowcount 0 and loses.
        """
        from datetime import datetime, timezone

        if not plaintext.startswith(RESET_TOKEN_PREFIX):
            return None
        token_hash = _hash_token(plaintext)
        with self._db.with_write() as db:
            cur = db.execute(
                "SELECT id, user_id, expires_at, used_at "
                "FROM password_reset_tokens WHERE token_hash = ?",
                (token_hash,),
            )
            row = cur.fetchone() if cur is not None else None
            if row is None:
                return None
            token_id, user_id, expires_at, used_at = row
            if used_at:
                return None
            try:
                exp = datetime.fromisoformat(str(expires_at).replace("Z", "+00:00"))
                if datetime.now(timezone.utc) > exp:
                    # Expired still burns: no window where a stale token could
                    # be replayed after the fact.
                    db.execute(
                        "UPDATE password_reset_tokens SET used_at = datetime('now') "
                        "WHERE id = ?",
                        (token_id,),
                    )
                    return None
            except ValueError:
                return None
            burn = db.execute(
                "UPDATE password_reset_tokens SET used_at = datetime('now') "
                "WHERE id = ? AND used_at IS NULL",
                (token_id,),
            )
            if burn is None or burn.rowcount != 1:
                return None
        return int(user_id)

    # ------------------------------------------------------------------
    def _one(self, sql: str, params: tuple) -> dict[str, Any] | None:
        cur = self._db.execute(sql, params)
        row = cur.fetchone() if cur is not None else None
        return self._row_to_user(row) if row is not None else None

    @staticmethod
    def _row_to_user(r: tuple) -> dict[str, Any]:
        return {
            "id": r[0],
            "username": r[1],
            "email": r[2],
            "role": r[3],
            "is_active": bool(r[4]),
            "created_at": r[5],
            "updated_at": r[6],
        }


def _loads(raw: str | None) -> list[str]:
    if not raw:
        return []
    import json

    try:
        return list(json.loads(raw))
    except (json.JSONDecodeError, TypeError):
        return []


__all__ = [
    "IdentityStore",
    "TOKEN_PREFIX",
    "RESET_TOKEN_PREFIX",
    "RESET_TOKEN_TTL_SECONDS",
    "SystemDBError",
]
