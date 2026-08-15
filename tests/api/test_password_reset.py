"""v1.10.5 M1 — one-time password reset tokens.

Admin issues a single-use token (``POST /admin/users/{id}/password-reset``,
plaintext returned exactly once, only the sha256 is stored); the user burns it
at ``POST /auth/password-reset`` which rotates the password hash, bumps
``token_valid_after`` (killing all outstanding JWTs) and writes an audit event.
"""

from __future__ import annotations

import pytest

pytest.importorskip("jwt")

from arrow_lake.system_db import Migrator, SystemDB
from arrow_lake.system_db.stores import IdentityStore

SECRET = "test-secret-key-min-32-chars-for-hmac!"


# ---------------------------------------------------------------------------
# IdentityStore persistence
# ---------------------------------------------------------------------------
@pytest.fixture
def identity() -> IdentityStore:
    db = SystemDB(":memory:")
    Migrator(db).run()
    yield IdentityStore(db)
    db.close()


class TestResetTokenStore:
    def test_create_returns_plaintext_once(self, identity: IdentityStore) -> None:
        uid = identity.create_user("alice", password_hash="h")
        plaintext, rec = identity.create_password_reset_token(uid)
        assert plaintext.startswith("alr_")
        assert rec["user_id"] == uid
        # Only the hash is persisted — the plaintext must not round-trip.
        stored = identity._db.execute(
            "SELECT token_hash FROM password_reset_tokens WHERE id = ?", (rec["id"],)
        ).fetchone()
        assert stored[0] != plaintext
        assert len(stored[0]) == 64  # sha256 hex

    def test_consume_happy_path(self, identity: IdentityStore) -> None:
        uid = identity.create_user("bob", password_hash="h")
        plaintext, _ = identity.create_password_reset_token(uid)
        assert identity.consume_password_reset_token(plaintext) == uid

    def test_token_is_single_use(self, identity: IdentityStore) -> None:
        uid = identity.create_user("carol", password_hash="h")
        plaintext, _ = identity.create_password_reset_token(uid)
        assert identity.consume_password_reset_token(plaintext) == uid
        assert identity.consume_password_reset_token(plaintext) is None

    def test_expired_token_rejected(self, identity: IdentityStore) -> None:
        uid = identity.create_user("dave", password_hash="h")
        plaintext, _ = identity.create_password_reset_token(uid, ttl_seconds=-60)
        assert identity.consume_password_reset_token(plaintext) is None

    def test_unknown_token_rejected(self, identity: IdentityStore) -> None:
        assert identity.consume_password_reset_token("alr_nope") is None

    def test_expired_token_still_burned(self, identity: IdentityStore) -> None:
        """An expired attempt marks the token used — no second chance window."""
        uid = identity.create_user("erin", password_hash="h")
        plaintext, _ = identity.create_password_reset_token(uid, ttl_seconds=-60)
        identity.consume_password_reset_token(plaintext)
        used = identity._db.execute(
            "SELECT used_at FROM password_reset_tokens "
            "WHERE user_id = ?", (uid,)
        ).fetchone()
        assert used[0] is not None

    def test_issuing_new_token_burns_siblings(self, identity: IdentityStore) -> None:
        """Single outstanding token per user — a re-issue kills the prior one."""
        uid = identity.create_user("frank", password_hash="h")
        first, _ = identity.create_password_reset_token(uid)
        second, _ = identity.create_password_reset_token(uid)
        assert identity.consume_password_reset_token(first) is None
        assert identity.consume_password_reset_token(second) == uid

    def test_create_purges_stale_rows(self, identity: IdentityStore) -> None:
        uid = identity.create_user("gina", password_hash="h")
        with identity._db.with_write() as db:
            db.execute(
                "INSERT INTO password_reset_tokens (user_id, token_hash, expires_at) "
                "VALUES (?, 'deadbeef', '2020-01-01T00:00:00Z')",
                (uid,),
            )
        identity.create_password_reset_token(uid)
        stale = identity._db.execute(
            "SELECT COUNT(*) FROM password_reset_tokens WHERE token_hash = 'deadbeef'"
        ).fetchone()
        assert stale[0] == 0

    def test_concurrent_double_spend_exactly_one_winner(self, identity: IdentityStore) -> None:
        """Two racing consumes of the same token — only one may burn it."""
        from concurrent.futures import ThreadPoolExecutor

        uid = identity.create_user("henry", password_hash="h")
        plaintext, _ = identity.create_password_reset_token(uid)
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(
                pool.map(identity.consume_password_reset_token, [plaintext, plaintext])
            )
        assert results.count(uid) == 1
        assert results.count(None) == 1

    def test_revoke_all_tokens(self, identity: IdentityStore) -> None:
        uid = identity.create_user("ivy", password_hash="h")
        pt1, _ = identity.create_token(uid, name="t1")
        pt2, _ = identity.create_token(uid, name="t2")
        other = identity.create_user("jack", password_hash="h")
        pt3, _ = identity.create_token(other, name="t3")
        assert identity.revoke_all_tokens(uid) == 2
        assert identity.validate_token(pt1) is None
        assert identity.validate_token(pt2) is None
        assert identity.validate_token(pt3) is not None  # other user untouched
        assert identity.revoke_all_tokens(uid) == 0  # idempotent


# ---------------------------------------------------------------------------
# End-to-end API flow
# ---------------------------------------------------------------------------
def _make_app():
    from unittest.mock import MagicMock

    from arrow_lake.api.app import create_app
    from arrow_lake.config import ArrowLakeConfig

    config = ArrowLakeConfig()
    config.auth.auth_mode = "jwt"
    config.auth.jwt_secret_key = SECRET
    config.api.api_key = ""
    # Hermetic: a live Redis on the host would make the lockout test count
    # cross-run failures from the shared ZSET bucket.
    config.redis.enabled = False
    app = create_app(config=config)
    app.state.lake = MagicMock()
    app.state.identity_store = IdentityStore(_mem_db())

    svc = app.state.auth_service
    store: IdentityStore = app.state.identity_store

    def _tva(sub: str) -> float | None:
        if not sub.isdigit():
            return None
        try:
            return store.get_token_valid_after(int(sub))
        except Exception:
            return None

    svc.set_token_valid_after_provider(_tva)
    return app


def _mem_db():
    db = SystemDB(":memory:")
    Migrator(db).run()
    return db


@pytest.mark.asyncio
async def test_admin_issues_user_consumes() -> None:
    from httpx import ASGITransport, AsyncClient

    from arrow_lake.api.auth_models import Role

    app = _make_app()
    store: IdentityStore = app.state.identity_store
    svc = app.state.auth_service
    from arrow_lake.api.passwords import hash_password

    admin_id = store.create_user("admin", role="admin", password_hash=hash_password("admin-pass-1"))
    uid = store.create_user("u1", role="viewer", password_hash=hash_password("old-pass-1"))
    admin_token = svc._encode(svc.create_access_token(user_id=str(admin_id), role=Role.ADMIN))
    # The user's outstanding JWT, issued BEFORE the reset.
    old_jwt = svc._encode(svc.create_access_token(user_id=str(uid), role=Role.VIEWER))

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as ac:
        issued = await ac.post(
            f"/api/v1/admin/users/{uid}/password-reset",
            headers={"Authorization": f"Bearer {admin_token}"},
        )
        assert issued.status_code == 200
        reset_token = issued.json()["token"]
        assert reset_token.startswith("alr_")
        assert issued.json()["expires_at"]

        # User burns the token with a new password.
        consumed = await ac.post(
            "/api/v1/auth/password-reset",
            json={"token": reset_token, "new_password": "new-pass-12"},
        )
        assert consumed.status_code == 200

        # Old password no longer logs in; new one does.
        bad = await ac.post("/api/v1/auth/login", json={"username": "u1", "password": "old-pass-1"})
        assert bad.status_code == 401
        good = await ac.post("/api/v1/auth/login", json={"username": "u1", "password": "new-pass-12"})
        assert good.status_code == 200

        # Pre-reset JWT is dead (token_valid_after bump).
        me = await ac.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {old_jwt}"})
        assert me.status_code == 401

        # The reset token cannot be used a second time.
        again = await ac.post(
            "/api/v1/auth/password-reset",
            json={"token": reset_token, "new_password": "another-pass-34"},
        )
        assert again.status_code == 401


@pytest.mark.asyncio
async def test_shared_api_key_identity_cannot_issue() -> None:
    """M1 review H1: the shared API key (sub='api-key', even with ADMIN role)
    must not mint reset tokens — that would outlive key rotation."""
    from httpx import ASGITransport, AsyncClient

    app = _make_app()
    svc = app.state.auth_service
    from arrow_lake.api.auth_models import Role

    shared_key_token = svc._encode(
        svc.create_access_token(user_id="api-key", role=Role.ADMIN)
    )
    store: IdentityStore = app.state.identity_store
    uid = store.create_user("target", role="viewer")

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as ac:
        r = await ac.post(
            f"/api/v1/admin/users/{uid}/password-reset",
            headers={"Authorization": f"Bearer {shared_key_token}"},
        )
        assert r.status_code == 403


@pytest.mark.asyncio
async def test_reset_revokes_personal_tokens_and_refresh() -> None:
    """M1 review: reset is incident response — personal tokens and refresh
    tokens die alongside the access JWTs."""
    from httpx import ASGITransport, AsyncClient

    from arrow_lake.api.auth_models import Role

    app = _make_app()
    store: IdentityStore = app.state.identity_store
    svc = app.state.auth_service
    from arrow_lake.api.passwords import hash_password

    admin_id = store.create_user("admin", role="admin")
    uid = store.create_user("u2", role="viewer", password_hash=hash_password("old-pass-1"))
    admin_token = svc._encode(svc.create_access_token(user_id=str(admin_id), role=Role.ADMIN))
    old_refresh = svc.create_refresh_token(user_id=str(uid), role=Role.VIEWER)
    pt, _ = store.create_token(uid, name="laptop")
    assert store.validate_token(pt) is not None  # sanity: valid pre-reset

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as ac:
        issued = await ac.post(
            f"/api/v1/admin/users/{uid}/password-reset",
            headers={"Authorization": f"Bearer {admin_token}"},
        )
        assert issued.status_code == 200
        consumed = await ac.post(
            "/api/v1/auth/password-reset",
            json={"token": issued.json()["token"], "new_password": "new-pass-12"},
        )
        assert consumed.status_code == 200

        # Refresh token (pre-reset) is dead too.
        refreshed = await ac.post("/api/v1/auth/refresh", json={"refresh_token": old_refresh})
        assert refreshed.status_code == 401

    # Personal token revoked in the store.
    assert store.validate_token(pt) is None


@pytest.mark.asyncio
async def test_deactivated_between_issue_and_consume() -> None:
    from httpx import ASGITransport, AsyncClient

    from arrow_lake.api.auth_models import Role

    app = _make_app()
    store: IdentityStore = app.state.identity_store
    svc = app.state.auth_service
    admin_id = store.create_user("admin", role="admin")
    uid = store.create_user("u3", role="viewer")
    admin_token = svc._encode(svc.create_access_token(user_id=str(admin_id), role=Role.ADMIN))

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as ac:
        issued = await ac.post(
            f"/api/v1/admin/users/{uid}/password-reset",
            headers={"Authorization": f"Bearer {admin_token}"},
        )
        assert issued.status_code == 200
        store.set_user_active(uid, False)  # deactivated after issuance
        consumed = await ac.post(
            "/api/v1/auth/password-reset",
            json={"token": issued.json()["token"], "new_password": "new-pass-12"},
        )
        assert consumed.status_code == 401


@pytest.mark.asyncio
async def test_issue_guards() -> None:
    from httpx import ASGITransport, AsyncClient

    from arrow_lake.api.auth_models import Role

    app = _make_app()
    store: IdentityStore = app.state.identity_store
    svc = app.state.auth_service
    admin_id = store.create_user("admin", role="admin")
    viewer_id = store.create_user("vx", role="viewer")
    admin_token = svc._encode(svc.create_access_token(user_id=str(admin_id), role=Role.ADMIN))
    viewer_token = svc._encode(svc.create_access_token(user_id=str(viewer_id), role=Role.VIEWER))

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as ac:
        # Unknown user → 404.
        missing = await ac.post(
            "/api/v1/admin/users/9999/password-reset",
            headers={"Authorization": f"Bearer {admin_token}"},
        )
        assert missing.status_code == 404

        # Deactivated user → rejected.
        gone = store.create_user("ghost", role="viewer")
        store.set_user_active(gone, False)
        inactive = await ac.post(
            f"/api/v1/admin/users/{gone}/password-reset",
            headers={"Authorization": f"Bearer {admin_token}"},
        )
        assert inactive.status_code == 422

        # Non-admin → 403.
        forbidden = await ac.post(
            f"/api/v1/admin/users/{viewer_id}/password-reset",
            headers={"Authorization": f"Bearer {viewer_token}"},
        )
        assert forbidden.status_code == 403


@pytest.mark.asyncio
async def test_consume_guards() -> None:
    from httpx import ASGITransport, AsyncClient

    app = _make_app()

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as ac:
        # Bad token → 401, short password → 422 (validation order aside, both rejected).
        bad = await ac.post(
            "/api/v1/auth/password-reset",
            json={"token": "alr_garbage", "new_password": "long-enough-1"},
        )
        assert bad.status_code == 401
        short = await ac.post(
            "/api/v1/auth/password-reset",
            json={"token": "alr_garbage", "new_password": "short"},
        )
        assert short.status_code in (401, 422)


@pytest.mark.asyncio
async def test_reset_endpoint_lockout() -> None:
    """Repeated bad reset attempts lock the source IP (login-lockout reuse)."""
    import arrow_lake.api.routers.auth as auth_router
    from httpx import ASGITransport, AsyncClient

    app = _make_app()
    # Deterministic in-memory lockout state.
    orig_limit = auth_router._LOGIN_FAIL_LIMIT
    auth_router._LOGIN_FAILURES.clear()
    auth_router._LOGIN_FAIL_LIMIT = 3

    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as ac:
            for _ in range(3):
                r = await ac.post(
                    "/api/v1/auth/password-reset",
                    json={"token": "alr_garbage", "new_password": "long-enough-1"},
                )
                assert r.status_code == 401
            locked = await ac.post(
                "/api/v1/auth/password-reset",
                json={"token": "alr_garbage", "new_password": "long-enough-1"},
            )
            assert locked.status_code == 429
    finally:
        auth_router._LOGIN_FAIL_LIMIT = orig_limit
        auth_router._LOGIN_FAILURES.clear()
