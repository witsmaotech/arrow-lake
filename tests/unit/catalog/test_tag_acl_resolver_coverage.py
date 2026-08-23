"""Cover missing lines in catalog/tag_acl_resolver.py — sync_tags_to_acls, _sync_table, REST fetch methods."""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest

from arrow_lake.catalog.tag_acl_resolver import TagAwareACLResolver
from arrow_lake.config.gravitino import GravitinoConfig


def _cfg(**overrides: object) -> GravitinoConfig:
    defaults = {
        "enabled": True,
        "uri": "http://g:8090",
        "metalake": "ml",
        "lance_catalog_name": "lance",
        "lance_schema_name": "arrow_lake",
        "tag_access_rules": {
            "pii": {"visible_to": ["admin"]},
            "sensitive": {"visible_to": ["admin", "editor"]},
        },
    }
    defaults.update(overrides)
    return GravitinoConfig(**defaults)


def _mock_resp(data: dict) -> MagicMock:
    resp = MagicMock()
    resp.read.return_value = json.dumps(data).encode()
    resp.__enter__ = lambda s: s
    resp.__exit__ = MagicMock(return_value=False)
    return resp


@pytest.fixture
def checker() -> MagicMock:
    return MagicMock()


@pytest.fixture
def resolver(checker: MagicMock) -> TagAwareACLResolver:
    return TagAwareACLResolver(config=_cfg(), checker=checker)


# ── sync_tags_to_acls ──


class TestSyncTagsToAcls:
    def test_no_rules_returns_zero(self, checker: MagicMock) -> None:
        cfg = _cfg(tag_access_rules={})
        resolver = TagAwareACLResolver(config=cfg, checker=checker)
        assert resolver.sync_tags_to_acls() == 0

    def test_no_tables_returns_zero(self, resolver: TagAwareACLResolver) -> None:
        with patch.object(resolver, "_list_gravitino_tables", return_value=[]):
            assert resolver.sync_tags_to_acls() == 0

    def test_syncs_multiple_tables(self, resolver: TagAwareACLResolver) -> None:
        with patch.object(resolver, "_list_gravitino_tables", return_value=["t1", "t2"]), \
             patch.object(resolver, "_sync_table", return_value=(2, {("t1", "viewer")})):
            total = resolver.sync_tags_to_acls()
        assert total == 4  # 2 per table

    def test_table_exception_continues(self, resolver: TagAwareACLResolver) -> None:
        call_count = 0

        def _sync_table_side_effect(table_name):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise RuntimeError("fail")
            return 1, set()

        with patch.object(resolver, "_list_gravitino_tables", return_value=["t1", "t2"]), \
             patch.object(resolver, "_sync_table", side_effect=_sync_table_side_effect):
            total = resolver.sync_tags_to_acls()
        assert total == 1


# ── _sync_table ──


class TestSyncTable:
    def test_no_column_tags_returns_zero(self, resolver: TagAwareACLResolver) -> None:
        with patch.object(resolver, "_fetch_column_tags", return_value={}):
            assert resolver._sync_table("tbl") == (0, set())

    def test_no_schema_returns_zero(self, resolver: TagAwareACLResolver) -> None:
        with patch.object(resolver, "_fetch_column_tags", return_value={"email": ["pii"]}), \
             patch.object(resolver, "_get_table_schema", return_value=[]):
            assert resolver._sync_table("tbl") == (0, set())

    def test_pii_tag_restricts_to_admin(self, resolver: TagAwareACLResolver, checker: MagicMock) -> None:
        column_tags = {"email": ["pii"], "name": []}
        schema = [{"name": "email"}, {"name": "name"}, {"name": "age"}]

        with patch.object(resolver, "_fetch_column_tags", return_value=column_tags), \
             patch.object(resolver, "_get_table_schema", return_value=schema):
            count, keys = resolver._sync_table("users")

        # pii on email → visible_to=[admin], denied = {editor, viewer}
        assert count == 2  # one ACL for editor, one for viewer
        assert keys == {("users", "editor"), ("users", "viewer")}
        checker.set_acl.assert_called()

    def test_sensitive_tag_restricts_to_admin_editor(self, resolver: TagAwareACLResolver, checker: MagicMock) -> None:
        column_tags = {"salary": ["sensitive"]}
        schema = [{"name": "salary"}, {"name": "dept"}]

        with patch.object(resolver, "_fetch_column_tags", return_value=column_tags), \
             patch.object(resolver, "_get_table_schema", return_value=schema):
            count, keys = resolver._sync_table("employees")

        # sensitive on salary → visible_to=[admin, editor], denied = {viewer}
        assert count == 1
        assert keys == {("employees", "viewer")}
        # Viewer should have salary hidden
        call_args = checker.set_acl.call_args[0][0]
        assert "salary" not in call_args.visible_columns

    def test_no_restricted_columns_returns_zero(self, resolver: TagAwareACLResolver) -> None:
        """Tags not in rules → no restrictions → return 0."""
        column_tags = {"col1": ["unknown_tag"]}
        schema = [{"name": "col1"}]

        with patch.object(resolver, "_fetch_column_tags", return_value=column_tags), \
             patch.object(resolver, "_get_table_schema", return_value=schema):
            assert resolver._sync_table("tbl") == (0, set())

    def test_set_acl_exception_continues(self, resolver: TagAwareACLResolver, checker: MagicMock) -> None:
        """If checker.set_acl raises, _sync_table continues and returns partial count."""
        column_tags = {"email": ["pii"], "phone": ["pii"]}
        schema = [{"name": "email"}, {"name": "phone"}, {"name": "name"}]

        call_num = 0

        def _set_acl_side_effect(acl):
            nonlocal call_num
            call_num += 1
            if call_num == 1:
                raise RuntimeError("acl error")

        checker.set_acl.side_effect = _set_acl_side_effect

        with patch.object(resolver, "_fetch_column_tags", return_value=column_tags), \
             patch.object(resolver, "_get_table_schema", return_value=schema):
            count, keys = resolver._sync_table("tbl")

        # pii → denied roles = {editor, viewer} → 2 ACLs
        # First set_acl fails, second succeeds
        assert count == 1
        assert keys == {("tbl", "viewer")}  # only the successful one is tracked


# ── _list_gravitino_tables ──


class TestListGravitinoTables:
    def test_fetch_returns_table_names(self, resolver: TagAwareACLResolver) -> None:
        data = {"identifiers": [{"name": "users"}, {"name": "orders"}]}

        with patch("urllib.request.Request") as MockReq, \
             patch("urllib.request.urlopen") as mock_open:
            MockReq.return_value = MagicMock()
            mock_open.return_value = _mock_resp(data)
            tables = resolver._list_gravitino_tables()

        assert tables == ["users", "orders"]

    def test_fetch_failure_returns_empty(self, resolver: TagAwareACLResolver) -> None:
        with patch("urllib.request.Request") as MockReq, \
             patch("urllib.request.urlopen", side_effect=RuntimeError("network")):
            MockReq.return_value = MagicMock()
            tables = resolver._list_gravitino_tables()

        assert tables == []


# ── _fetch_column_tags ──


class TestFetchColumnTags:
    def test_fetch_returns_column_tags(self, resolver: TagAwareACLResolver) -> None:
        mock_svc = MagicMock()
        mock_svc.list_column_tags.return_value = {"email": ["pii"], "salary": ["sensitive"]}

        with patch("arrow_lake.quality.gravitino_tags.GravitinoTagService", return_value=mock_svc):
            tags = resolver._fetch_column_tags("users")

        assert tags == {"email": ["pii"], "salary": ["sensitive"]}

    def test_fetch_failure_raises(self, resolver: TagAwareACLResolver) -> None:
        """v1.10.7 WP6: failures propagate so the syncer keeps last-known ACLs."""
        with patch("arrow_lake.quality.gravitino_tags.GravitinoTagService", side_effect=RuntimeError("fail")):
            with pytest.raises(RuntimeError):
                resolver._fetch_column_tags("users")


# ── _get_table_schema ──


class TestGetTableSchema:
    def test_fetch_returns_columns(self, resolver: TagAwareACLResolver) -> None:
        data = {"table": {"columns": [{"name": "id"}, {"name": "email"}]}}

        with patch("urllib.request.Request") as MockReq, \
             patch("urllib.request.urlopen") as mock_open:
            MockReq.return_value = MagicMock()
            mock_open.return_value = _mock_resp(data)
            schema = resolver._get_table_schema("users")

        assert len(schema) == 2
        assert schema[0]["name"] == "id"

    def test_fetch_failure_returns_empty(self, resolver: TagAwareACLResolver) -> None:
        with patch("urllib.request.Request") as MockReq, \
             patch("urllib.request.urlopen", side_effect=RuntimeError("fail")):
            MockReq.return_value = MagicMock()
            schema = resolver._get_table_schema("users")

        assert schema == []
