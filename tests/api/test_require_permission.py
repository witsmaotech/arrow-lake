"""v1.10.5 M4 — scope-based authorization (``require_permission``).

Tokens minted since v1.10.5 carry a ``permissions`` claim (filled from the
role matrix at login); when the claim is non-empty the check is exact (least
privilege). Tokens with an empty claim — pre-v1.10.5 JWTs, the shared api-key
identity, scope-less personal tokens — fall back to the role hierarchy, so
both generations of credentials behave exactly as before.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

pytest.importorskip("jwt")

import jwt as pyjwt
from httpx import ASGITransport, AsyncClient

from arrow_lake.api.auth_models import Role
from arrow_lake.api.app import create_app
from arrow_lake.api.rbac import Permission
from arrow_lake.config import ArrowLakeConfig
from arrow_lake.system_db import Migrator, SystemDB
from arrow_lake.system_db.stores import IdentityStore

SECRET = "test-secret-key-min-32-chars-for-hmac!"


def _make_app():
    config = ArrowLakeConfig()
    config.auth.auth_mode = "jwt"
    config.auth.jwt_secret_key = SECRET
    # A non-empty api_key registers the api-key middleware (personal-token
    # resolution path); Bearer requests pass through it untouched. The
    # identity store itself is injected manually below — no real system_db.
    config.api.api_key = "dummy-shared-key"
    config.redis.enabled = False
    app = create_app(config=config)
    app.state.lake = MagicMock()
    db = SystemDB(":memory:")
    Migrator(db).run()
    app.state.identity_store = IdentityStore(db)

    svc = app.state.auth_service

    def _tva(sub: str) -> float | None:
        return None

    svc.set_token_valid_after_provider(_tva)
    return app


@pytest.fixture
def app():
    return _make_app()


def _token(app, role: Role, permissions: list[str] | None = None) -> str:
    svc = app.state.auth_service
    return svc._encode(
        svc.create_access_token(user_id="42", role=role, permissions=permissions)
    )


# ---------------------------------------------------------------------------
# The dependency factory (isolated probe route)
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_empty_permissions_falls_back_to_role_editor_passes(app) -> None:
    from fastapi import Depends

    from arrow_lake.api.deps import require_permission

    app.get("/__perm_probe")(
        lambda user=Depends(require_permission(Permission.DATASET_WRITE)): {"sub": user.sub}
    )
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as ac:
        r = await ac.get("/__perm_probe", headers={"Authorization": f"Bearer {_token(app, Role.EDITOR)}"})
        assert r.status_code == 200


@pytest.mark.asyncio
async def test_empty_permissions_falls_back_to_role_viewer_denied(app) -> None:
    from fastapi import Depends

    from arrow_lake.api.deps import require_permission

    app.get("/__perm_probe")(
        lambda user=Depends(require_permission(Permission.DATASET_WRITE)): {"sub": user.sub}
    )
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as ac:
        r = await ac.get("/__perm_probe", headers={"Authorization": f"Bearer {_token(app, Role.VIEWER)}"})
        assert r.status_code == 403


@pytest.mark.asyncio
async def test_exact_claim_grants_below_role(app) -> None:
    """Claim wins over role: viewer-role token carrying dataset:write passes."""
    from fastapi import Depends

    from arrow_lake.api.deps import require_permission

    app.get("/__perm_probe")(
        lambda user=Depends(require_permission(Permission.DATASET_WRITE)): {"sub": user.sub}
    )
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as ac:
        r = await ac.get(
            "/__perm_probe",
            headers={
                "Authorization": f"Bearer {_token(app, Role.VIEWER, ['dataset:write'])}"
            },
        )
        assert r.status_code == 200


@pytest.mark.asyncio
async def test_exact_claim_restricts_above_role(app) -> None:
    """Claim wins over role: editor-role token without dataset:delete is denied."""
    from fastapi import Depends

    from arrow_lake.api.deps import require_permission

    app.get("/__perm_probe")(
        lambda user=Depends(require_permission(Permission.DATASET_DELETE)): {"sub": user.sub}
    )
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as ac:
        r = await ac.get(
            "/__perm_probe",
            headers={
                "Authorization": f"Bearer {_token(app, Role.EDITOR, ['dataset:read', 'dataset:write'])}"
            },
        )
        assert r.status_code == 403


# ---------------------------------------------------------------------------
# Login fills the permissions claim from the role matrix
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_login_mints_token_with_role_permissions(app) -> None:
    from arrow_lake.api.passwords import hash_password

    store: IdentityStore = app.state.identity_store
    store.create_user("ed", role="editor", password_hash=hash_password("editor-pass-1"))
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as ac:
        r = await ac.post("/api/v1/auth/login", json={"username": "ed", "password": "editor-pass-1"})
        assert r.status_code == 200
        access = r.json()["access_token"]
    claim = pyjwt.decode(access, SECRET, algorithms=["HS256"], options={"verify_aud": False})
    assert set(claim["permissions"]) == {
        "dataset:read", "dataset:write", "dataset:delete",
    }


# ---------------------------------------------------------------------------
# The real sensitive endpoints actually enforce the permission
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_delete_dataset_enforces_scope(app) -> None:
    """New viewer token (claim=[dataset:read]) → 403 on DELETE /datasets/{name}."""
    from arrow_lake.api.passwords import hash_password

    store: IdentityStore = app.state.identity_store
    store.create_user("vw", role="viewer", password_hash=hash_password("viewer-pass-1"))
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as ac:
        login = await ac.post("/api/v1/auth/login", json={"username": "vw", "password": "viewer-pass-1"})
        token = login.json()["access_token"]
        r = await ac.delete(
            "/api/v1/datasets/some_ds",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert r.status_code == 403


@pytest.mark.asyncio
async def test_old_editor_token_still_deletes(app) -> None:
    """Pre-v1.10.5 token (empty claim) + EDITOR → auth passes (fallback)."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as ac:
        r = await ac.delete(
            "/api/v1/datasets/some_ds",
            headers={"Authorization": f"Bearer {_token(app, Role.EDITOR)}"},
        )
        assert r.status_code != 403  # auth layer passed; downstream may 404/422 on mock lake


# ---------------------------------------------------------------------------
# Personal tokens: scopes are plumbed into the claim (empty → fallback)
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_personal_token_scopes_enforced() -> None:
    """alp_ tokens: scopes surface as the permissions claim (exact check);
    scope-less tokens keep the role-hierarchy fallback."""
    from fastapi import Depends

    from arrow_lake.api.deps import require_permission

    # api_key mode: no JWT middleware (which would 401 non-Bearer requests),
    # so the X-API-Key personal-token path is the auth surface under test.
    config = ArrowLakeConfig()
    config.auth.auth_mode = "api_key"
    config.auth.jwt_secret_key = ""
    config.api.api_key = "dummy-shared-key"
    config.redis.enabled = False
    app = create_app(config=config)
    app.state.lake = MagicMock()
    db = SystemDB(":memory:")
    Migrator(db).run()
    store = IdentityStore(db)
    app.state.identity_store = store

    app.get("/__perm_probe")(
        lambda user=Depends(require_permission(Permission.DATASET_WRITE)): {"sub": user.sub}
    )
    uid = store.create_user("pt-user", role="editor")
    read_only, _ = store.create_token(uid, name="ro", scopes=["dataset:read"])
    scopeless, _ = store.create_token(uid, name="bare")  # scopes=None

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as ac:
        # Scoped token: exact claim — write denied despite editor role.
        denied = await ac.get("/__perm_probe", headers={"X-API-Key": read_only})
        assert denied.status_code == 403
        # Scope-less token: empty claim → role fallback → passes.
        allowed = await ac.get("/__perm_probe", headers={"X-API-Key": scopeless})
        assert allowed.status_code == 200
