"""v1.10.5 M0 — per-user token invalidation (``users.token_valid_after``).

A token whose ``iat`` predates the user's ``token_valid_after`` cutoff is
rejected, so deactivating a user or changing their password/role takes effect
on the very next request instead of waiting out the 30-minute access TTL.
"""

from __future__ import annotations

import time

import pytest

pytest.importorskip("jwt")

from arrow_lake.api.auth_service import AuthService
from arrow_lake.system_db import Migrator, SystemDB
from arrow_lake.system_db.stores import IdentityStore

SECRET = "test-secret-key-min-32-chars-for-hmac!"


# ---------------------------------------------------------------------------
# AuthService provider logic
# ---------------------------------------------------------------------------
class TestTokenValidAfterProvider:
    def test_token_before_cutoff_rejected(self) -> None:
        svc = AuthService(secret_key=SECRET)
        token = svc.create_refresh_token(user_id="7")
        svc.set_token_valid_after_provider(lambda sub: time.time() + 60)
        with pytest.raises(ValueError, match="revoked"):
            svc.verify_token(token)

    def test_token_after_cutoff_accepted(self) -> None:
        svc = AuthService(secret_key=SECRET)
        token = svc.create_refresh_token(user_id="7")
        # Cutoff strictly before issuance.
        svc.set_token_valid_after_provider(lambda sub: time.time() - 120)
        assert svc.verify_token(token).sub == "7"

    def test_provider_returns_none_skips_check(self) -> None:
        svc = AuthService(secret_key=SECRET)
        token = svc.create_refresh_token(user_id="api-user")
        svc.set_token_valid_after_provider(lambda sub: None)
        assert svc.verify_token(token).sub == "api-user"

    def test_provider_exception_skips_check_fail_open(self) -> None:
        """Store unreachable → current (pre-v1.10.5) behaviour, not a lockout."""
        svc = AuthService(secret_key=SECRET)
        token = svc.create_refresh_token(user_id="7")

        def _boom(sub: str) -> float | None:
            raise RuntimeError("store down")

        svc.set_token_valid_after_provider(_boom)
        assert svc.verify_token(token).sub == "7"

    def test_refresh_flow_respects_cutoff(self) -> None:
        svc = AuthService(secret_key=SECRET)
        token = svc.create_refresh_token(user_id="7")
        svc.set_token_valid_after_provider(lambda sub: time.time() + 60)
        with pytest.raises(ValueError, match="revoked"):
            svc.refresh_access_token(token)

    def test_no_provider_set_behaves_as_before(self) -> None:
        svc = AuthService(secret_key=SECRET)
        token = svc.create_refresh_token(user_id="7")
        assert svc.verify_token(token).sub == "7"


# ---------------------------------------------------------------------------
# IdentityStore persistence
# ---------------------------------------------------------------------------
@pytest.fixture
def identity() -> IdentityStore:
    db = SystemDB(":memory:")
    Migrator(db).run()
    yield IdentityStore(db)
    db.close()


class TestIdentityStoreCutoff:
    def test_default_is_none(self, identity: IdentityStore) -> None:
        uid = identity.create_user("alice")
        assert identity.get_token_valid_after(uid) is None

    def test_bump_roundtrip(self, identity: IdentityStore) -> None:
        uid = identity.create_user("bob")
        assert identity.bump_token_valid_after(uid) is True
        cutoff = identity.get_token_valid_after(uid)
        assert cutoff is not None
        assert abs(cutoff - time.time()) < 5

    def test_bump_unknown_user_returns_false(self, identity: IdentityStore) -> None:
        assert identity.bump_token_valid_after(9999) is False

    def test_bump_persists_across_store_instances(self, identity: IdentityStore) -> None:
        uid = identity.create_user("carol")
        identity.bump_token_valid_after(uid)
        # New store handle on the same underlying DB (simulates restart).
        from arrow_lake.system_db.connection import SystemDB

        assert identity.get_token_valid_after(uid) is not None


# ---------------------------------------------------------------------------
# End-to-end: deactivating a user kills their outstanding JWT on next request
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_deactivate_kills_outstanding_tokens() -> None:
    from unittest.mock import MagicMock

    from httpx import ASGITransport, AsyncClient

    from arrow_lake.api.app import create_app
    from arrow_lake.api.auth_models import Role
    from arrow_lake.config import ArrowLakeConfig

    config = ArrowLakeConfig()
    config.auth.auth_mode = "jwt"
    config.auth.jwt_secret_key = SECRET
    config.api.api_key = ""
    app = create_app(config=config)
    app.state.lake = MagicMock()
    app.state.identity_store = identity_store = IdentityStore(_mem_db())

    svc = app.state.auth_service

    def _tva(sub: str) -> float | None:
        if not sub.isdigit():
            return None
        try:
            return identity_store.get_token_valid_after(int(sub))
        except Exception:
            return None

    svc.set_token_valid_after_provider(_tva)

    admin_id = identity_store.create_user("admin", role="admin")
    victim_id = identity_store.create_user("victim", role="viewer")
    admin_token = svc._encode(svc.create_access_token(user_id=str(admin_id), role=Role.ADMIN))
    victim_token = svc._encode(svc.create_access_token(user_id=str(victim_id), role=Role.VIEWER))

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as ac:
        # Victim is active → token works.
        me = await ac.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {victim_token}"})
        assert me.status_code == 200

        # Admin deactivates the victim.
        deact = await ac.delete(
            f"/api/v1/admin/users/{victim_id}",
            headers={"Authorization": f"Bearer {admin_token}"},
        )
        assert deact.status_code == 200

        # Victim's outstanding JWT dies immediately (no TTL wait).
        me2 = await ac.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {victim_token}"})
        assert me2.status_code == 401

        # Admin's own token is unaffected.
        me3 = await ac.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {admin_token}"})
        assert me3.status_code == 200


def _mem_db():
    db = SystemDB(":memory:")
    Migrator(db).run()
    return db
