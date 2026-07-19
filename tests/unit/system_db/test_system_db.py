"""Unit tests for ``arrow_lake.system_db`` (v1.9.0 control-plane DB).

Covers the libSQL connection, the migration runner, the RBAC + identity
stores, PermissionChecker store-backed integration (incl. persistence and
the no-store fallback regression), the TTL cache, and the fail-close path.

All tests use ``:memory:`` libSQL — no container dependency, fast.
"""

from __future__ import annotations

import time

import pytest

from arrow_lake.api.auth_models import Role
from arrow_lake.api.rbac import (
    DatasetACL,
    PermissionChecker,
    SchemaACL,
    _ROLE_PERMISSIONS,
)
from arrow_lake.system_db import Migrator, SystemDB, SystemDBError
from arrow_lake.system_db.stores import FailMode, IdentityStore, RbacStore, TTLCache


# --------------------------------------------------------------------------- #
# Fixtures
# --------------------------------------------------------------------------- #
@pytest.fixture
def db() -> SystemDB:
    conn = SystemDB(":memory:")
    Migrator(conn).run()
    yield conn
    conn.close()


@pytest.fixture
def rbac(db: SystemDB) -> RbacStore:
    store = RbacStore(db, cache_ttl=0)
    store.seed_role_permissions(_ROLE_PERMISSIONS)
    return store


@pytest.fixture
def identity(db: SystemDB) -> IdentityStore:
    return IdentityStore(db)


# --------------------------------------------------------------------------- #
# SystemDB + Migrator
# --------------------------------------------------------------------------- #
class TestSystemDBConnection:
    def test_connect_memory_and_execute(self) -> None:
        conn = SystemDB(":memory:")
        try:
            cur = conn.execute("SELECT 1 + 1")
            assert cur.fetchone()[0] == 2
        finally:
            conn.close()

    def test_health_true_when_up(self, db: SystemDB) -> None:
        assert db.health() is True

    def test_connect_failure_raises_system_db_error(self) -> None:
        # Unreachable http endpoint → exhausts retry budget → SystemDBError.
        with pytest.raises(SystemDBError):
            SystemDB("http://127.0.0.1:1", connect_timeout_seconds=0.2)

    def test_execute_reconnects_on_dead_connection(self) -> None:
        """bug1 fix: a stale/dead cached connection auto-reconnects.

        libSQL http connections die after long idle; without reconnect every
        control-plane query silently fails (only RbacStore's serve_stale masks
        it). ``execute`` must rebuild the connection once and retry so that
        identity/token/task/user-state keep working without an api restart.
        """
        db = SystemDB(":memory:")
        reconnects = {"n": 0}
        real_connect = db._connect_with_retry

        def counting_connect():
            reconnects["n"] += 1
            return real_connect()

        class _DeadConn:
            def execute(self, *a, **k):
                raise RuntimeError("connection reset by peer")

            def commit(self):
                raise RuntimeError("connection reset by peer")

            def close(self):
                pass

        db._connect_with_retry = counting_connect
        db._conn = _DeadConn()  # cached connection is dead

        # execute: dead.execute raises → _reconnect (counting_connect) → fresh → ok
        row = db.execute("SELECT 1").fetchone()
        assert row[0] == 1
        assert reconnects["n"] == 1
        db.close()


class TestMigrator:
    def test_creates_rbac_tables(self, db: SystemDB) -> None:
        cur = db.execute(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
        )
        tables = {r[0] for r in cur.fetchall()}
        for required in {
            "users",
            "personal_tokens",
            "role_permissions",
            "dataset_acl_grants",
            "dataset_row_col_acls",
            "schema_acls",
            "acl_denies",
            "schema_version",
        }:
            assert required in tables

    def test_idempotent_rerun(self, db: SystemDB) -> None:
        # Running again applies nothing new and does not error.
        applied = Migrator(db).run()
        assert applied == []
        # schema_version still has exactly the recorded versions.
        versions = Migrator(db).applied_versions()
        assert versions == {1, 2, 3, 4}


# --------------------------------------------------------------------------- #
# RbacStore
# --------------------------------------------------------------------------- #
class TestRbacStore:
    def test_fail_mode_is_fail_close(self, rbac: RbacStore) -> None:
        assert rbac.fail_mode == FailMode.FAIL_CLOSE

    def test_seed_and_get_role_perms(self, rbac: RbacStore) -> None:
        assert "admin:manage" in rbac.get_role_permissions("admin")
        assert rbac.get_role_permissions("viewer") == frozenset({"dataset:read"})
        # unknown role → empty
        assert rbac.get_role_permissions("ghost") == frozenset()

    def test_dataset_grant_and_revoke(self, rbac: RbacStore) -> None:
        rbac.grant_dataset_access("ds1", "editor", "read")
        rbac.grant_dataset_access("ds1", "editor", "write")
        grants = rbac.get_dataset_grants("ds1")
        assert grants["editor"] == {"read", "write"}
        removed = rbac.revoke_dataset_access("ds1", "editor")
        assert removed == 2
        assert rbac.get_dataset_grants("ds1") == {}

    def test_row_col_acl_upsert_and_delete(self, rbac: RbacStore) -> None:
        rbac.set_row_col_acl(
            "ds1", "viewer", visible_columns=["a", "b"], denied_actions=["delete"]
        )
        acl = rbac.get_row_col_acl("ds1", "viewer")
        assert acl is not None
        assert acl["visible_columns"] == frozenset({"a", "b"})
        assert acl["denied_actions"] == frozenset({"delete"})
        # upsert replaces
        rbac.set_row_col_acl("ds1", "viewer", visible_columns=["c"])
        assert rbac.get_row_col_acl("ds1", "viewer")["visible_columns"] == frozenset({"c"})
        assert rbac.delete_row_col_acl("ds1", "viewer") is True
        assert rbac.get_row_col_acl("ds1", "viewer") is None

    def test_schema_acl_upsert_and_delete(self, rbac: RbacStore) -> None:
        rbac.set_schema_acl("ns", "viewer", allowed_actions=["read"])
        acl = rbac.get_schema_acl("ns", "viewer")
        assert acl is not None and "read" in acl["allowed_actions"]
        assert rbac.delete_schema_acl("ns", "viewer") is True
        assert rbac.get_schema_acl("ns", "viewer") is None

    def test_deny_add_and_remove(self, rbac: RbacStore) -> None:
        rbac.deny_action("ds1", "delete", reason="policy")
        assert rbac.list_denies("ds1") == {"delete"}
        assert rbac.remove_deny("ds1", "delete") is True
        assert rbac.list_denies("ds1") == set()
        assert rbac.remove_deny("ds1", "delete") is False

    def test_cache_invalidates_on_write(self, db: SystemDB) -> None:
        store = RbacStore(db, cache_ttl=60)
        store.grant_dataset_access("ds1", "editor", "read")
        # populate cache
        assert store.get_dataset_grants("ds1")["editor"] == {"read"}
        # mutate → cache must reflect the new state
        store.grant_dataset_access("ds1", "editor", "write")
        assert store.get_dataset_grants("ds1")["editor"] == {"read", "write"}


# --------------------------------------------------------------------------- #
# IdentityStore
# --------------------------------------------------------------------------- #
class TestIdentityStore:
    def test_create_and_list_users(self, identity: IdentityStore) -> None:
        uid = identity.create_user("alice", email="a@x.io", role="admin")
        assert uid > 0
        names = [u["username"] for u in identity.list_users()]
        assert "alice" in names
        assert identity.get_user_by_username("alice")["role"] == "admin"

    def test_token_validate_happy_path(self, identity: IdentityStore) -> None:
        uid = identity.create_user("bob", role="editor")
        plaintext, rec = identity.create_token(uid, name="ci", scopes=["dataset:read"])
        assert plaintext.startswith("al_")
        resolved = identity.validate_token(plaintext)
        assert resolved is not None
        assert resolved["username"] == "bob"
        assert resolved["role"] == "editor"
        assert resolved["scopes"] == ["dataset:read"]

    def test_token_bad_returns_none(self, identity: IdentityStore) -> None:
        identity.create_user("bob", role="editor")
        assert identity.validate_token("al_definitely_wrong") is None
        assert identity.validate_token("not-even-a-token") is None

    def test_token_revoked_blocks(self, identity: IdentityStore) -> None:
        uid = identity.create_user("bob", role="editor")
        plaintext, rec = identity.create_token(uid, name="ci")
        assert identity.validate_token(plaintext) is not None
        assert identity.revoke_token(rec["id"]) is True
        assert identity.validate_token(plaintext) is None

    def test_token_expired_blocks(self, identity: IdentityStore) -> None:
        uid = identity.create_user("bob", role="editor")
        plaintext, _ = identity.create_token(
            uid, name="ci", expires_at="2000-01-01T00:00:00Z"
        )
        assert identity.validate_token(plaintext) is None

    def test_inactive_user_blocks(self, identity: IdentityStore) -> None:
        uid = identity.create_user("bob", role="editor")
        plaintext, _ = identity.create_token(uid, name="ci")
        identity.set_user_active(uid, False)
        assert identity.validate_token(plaintext) is None

    def test_token_hash_not_plaintext(self, identity: IdentityStore) -> None:
        uid = identity.create_user("bob", role="editor")
        plaintext, _ = identity.create_token(uid, name="ci")
        # The DB must never store the plaintext token.
        from arrow_lake.system_db.stores.identity import _hash_token

        cur = identity._db.execute("SELECT token_hash FROM personal_tokens")
        stored = cur.fetchone()[0]
        assert stored == _hash_token(plaintext)
        assert plaintext not in stored

    def test_update_user(self, identity: IdentityStore) -> None:
        from arrow_lake.api.passwords import hash_password

        uid = identity.create_user(
            "carol", role="viewer", password_hash=hash_password("oldpass123")
        )
        # patch role + email + is_active
        assert identity.update_user(uid, role="editor", email="c@x.io", is_active=False) is True
        users = {u["username"]: u for u in identity.list_users()}
        assert users["carol"]["role"] == "editor"
        assert users["carol"]["email"] == "c@x.io"
        assert users["carol"]["is_active"] is False
        # patch password (pbkdf2 uses random salt → only assert it changed)
        old_hash = identity.get_user_with_credentials("carol")["password_hash"]
        assert identity.update_user(uid, password_hash=hash_password("newpass456")) is True
        new_hash = identity.get_user_with_credentials("carol")["password_hash"]
        assert new_hash != old_hash
        # no-op (no fields) → False
        assert identity.update_user(uid) is False


# --------------------------------------------------------------------------- #
# PermissionChecker — store-backed integration + fallback regression
# --------------------------------------------------------------------------- #
class TestPermissionCheckerStoreIntegration:
    def test_store_backed_role_perms(self, rbac: RbacStore) -> None:
        ck = PermissionChecker(rbac_store=rbac)
        assert ck.has_permission(Role.ADMIN, "admin:manage") is True
        assert ck.has_permission(Role.VIEWER, "dataset:write") is False

    def test_store_backed_grant_and_deny(self, rbac: RbacStore) -> None:
        ck = PermissionChecker(rbac_store=rbac)
        ck.grant_dataset_access("dsA", Role.VIEWER, "read")
        assert ck.check_dataset_access(role=Role.VIEWER, dataset="dsA", action="read")
        ck.deny_action("dsA", "read")
        assert not ck.check_dataset_access(
            role=Role.VIEWER, dataset="dsA", action="read"
        )

    def test_store_backed_row_col_and_schema_acl(self, rbac: RbacStore) -> None:
        ck = PermissionChecker(rbac_store=rbac)
        ck.set_acl(DatasetACL(dataset="dsB", role="viewer", visible_columns=frozenset({"x"})))
        acl = ck.get_acl("dsB", Role.VIEWER)
        assert acl is not None and acl.visible_columns == frozenset({"x"})
        ck.set_schema_acl(SchemaACL(schema="ns", role="viewer", allowed_actions=frozenset({"read"})))
        assert "read" in ck.get_schema_acl("ns", Role.VIEWER).allowed_actions  # type: ignore[union-attr]

    def test_persistence_across_instances(self, rbac: RbacStore) -> None:
        # The whole point of v1.9: a fresh checker sees persisted state.
        PermissionChecker(rbac_store=rbac).deny_action("dsA", "read")
        ck2 = PermissionChecker(rbac_store=rbac)
        assert not ck2.check_dataset_access(
            role=Role.VIEWER, dataset="dsA", action="read"
        )

    def test_no_store_keeps_inmemory_behavior(self) -> None:
        # Regression: pre-v1.9 path must be unchanged.
        ck = PermissionChecker()
        ck.grant_dataset_access("dsX", Role.VIEWER, "read")
        assert ck.check_dataset_access(role=Role.VIEWER, dataset="dsX", action="read")
        assert ck._store is None  # noqa: SLF001


# --------------------------------------------------------------------------- #
# TTLCache
# --------------------------------------------------------------------------- #
class TestTTLCache:
    def test_get_set_invalidate(self) -> None:
        cache = TTLCache(ttl_seconds=60)
        assert cache.get("k") is None
        cache.set("k", "v")
        assert cache.get("k") == "v"
        cache.invalidate("k")
        assert cache.get("k") is None

    def test_zero_ttl_disables(self) -> None:
        cache = TTLCache(ttl_seconds=0)
        cache.set("k", "v")
        assert cache.get("k") is None

    def test_expiry(self) -> None:
        cache = TTLCache(ttl_seconds=0.05)
        cache.set("k", "v")
        assert cache.get("k") == "v"
        time.sleep(0.07)
        assert cache.get("k") is None

    def test_invalidate_all(self) -> None:
        cache = TTLCache(ttl_seconds=60)
        cache.set("a", 1)
        cache.set("b", 2)
        cache.invalidate()
        assert cache.get("a") is None and cache.get("b") is None
