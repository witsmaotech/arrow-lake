"""P3 store tests (v1.9.0): UserStateStore (saved queries / dashboards /
favorites / preferences / notifications) + IdentityStore.list_users
(backing the admin user-management endpoint)."""

from __future__ import annotations

import pytest

from arrow_lake.system_db import Migrator, SystemDB
from arrow_lake.system_db.stores import IdentityStore, UserStateStore


@pytest.fixture
def db() -> SystemDB:
    conn = SystemDB(":memory:")
    Migrator(conn).run()
    yield conn
    conn.close()


@pytest.fixture
def user_id(db: SystemDB) -> int:
    return IdentityStore(db).create_user("alice", role="editor")


class TestUserStateStore:
    def test_saved_queries_crud(self, db: SystemDB, user_id: int) -> None:
        us = UserStateStore(db)
        qid = us.save_query(user_id, "top", "SELECT 1", query_type="sql", dataset="d")
        assert us.list_queries(user_id)[0]["name"] == "top"
        # public visibility: another user sees public, not private
        other = IdentityStore(db).create_user("bob")
        us.save_query(other, "priv", "SELECT 2", is_public=False)
        pub_qid = us.save_query(other, "pubq", "SELECT 3", is_public=True)
        seen = {q["name"] for q in us.list_queries(user_id, include_public=True)}
        assert "pubq" in seen and "priv" not in seen
        assert us.delete_query(user_id, qid) is True
        assert us.delete_query(user_id, qid) is False

    def test_dashboards_crud(self, db: SystemDB, user_id: int) -> None:
        us = UserStateStore(db)
        did = us.save_dashboard(user_id, "dash", {"w": [1, 2]})
        ds = us.list_dashboards(user_id)
        assert len(ds) == 1 and ds[0]["layout"] == {"w": [1, 2]}
        assert us.delete_dashboard(user_id, did) is True

    def test_favorites_unique(self, db: SystemDB, user_id: int) -> None:
        us = UserStateStore(db)
        assert us.add_favorite(user_id, "dataset", "d1") is True
        assert us.add_favorite(user_id, "dataset", "d1") is False  # unique
        assert len(us.list_favorites(user_id)) == 1
        assert us.remove_favorite(user_id, "dataset", "d1") is True

    def test_preferences_upsert(self, db: SystemDB, user_id: int) -> None:
        us = UserStateStore(db)
        assert us.get_preferences(user_id) == {}
        us.set_preferences(user_id, {"theme": "dark"})
        assert us.get_preferences(user_id) == {"theme": "dark"}
        us.set_preferences(user_id, {"theme": "light", "lang": "zh"})
        assert us.get_preferences(user_id) == {"theme": "light", "lang": "zh"}

    def test_notifications(self, db: SystemDB, user_id: int) -> None:
        us = UserStateStore(db)
        n1 = us.notify(user_id, "hello")
        us.notify(user_id, "warn", kind="warning")
        assert us.unread_count(user_id) == 2
        all_n = us.list_notifications(user_id)
        assert len(all_n) == 2 and all_n[0]["read"] is False
        assert us.mark_read(user_id, n1) == 1
        assert us.unread_count(user_id) == 1
        unread = us.list_notifications(user_id, unread_only=True)
        assert len(unread) == 1
        assert us.mark_read(user_id) == 1  # mark all
        assert us.unread_count(user_id) == 0


class TestIdentityListUsers:
    def test_list_users_backs_admin_endpoint(self, db: SystemDB) -> None:
        idn = IdentityStore(db)
        idn.create_user("alice", role="admin")
        idn.create_user("bob", role="editor")
        users = idn.list_users()
        assert {u["username"] for u in users} == {"alice", "bob"}
        assert all("role" in u and "is_active" in u for u in users)
