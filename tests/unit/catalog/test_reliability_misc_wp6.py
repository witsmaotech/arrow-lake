"""v1.10.7 WP6 (review H11/H12 + embed guard): reliability misc.

- H11: a failed duckdb.connect must give the pool permit back — otherwise
  every failed acquire permanently shrinks the pool to zero.
- H12: tag→ACL sync must RECOVER (remove stale ACLs when tags are lifted)
  and must NOT treat a failed tag fetch as "no tags" (which would nuke
  restrictions on a Gravitino hiccup).
- embed guard: the backfill de-dup guard must consult the Redis mirror so
  worker B doesn't start a second backfill while worker A runs one.
"""

from __future__ import annotations

import pytest

from arrow_lake.catalog.connection_pool import DuckDBConnectionPool


class TestPoolAcquireFailureRollback:
    def test_failed_connect_returns_permit(self, monkeypatch):
        pool = DuckDBConnectionPool(max_size=2, timeout=1.0)
        calls = {"n": 0}

        def boom(_db):
            calls["n"] += 1
            raise OSError("no duckdb here")

        monkeypatch.setattr("arrow_lake.catalog.connection_pool.duckdb.connect", boom)

        for _ in range(5):
            with pytest.raises(OSError):
                pool.acquire()

        health = pool.health()
        assert health.active_connections == 0  # H11: no leaked slots
        assert calls["n"] == 5

        # pool still fully usable: a working connect acquires immediately
        monkeypatch.setattr(
            "arrow_lake.catalog.connection_pool.duckdb.connect",
            lambda _db: "conn",
        )
        conn = pool.acquire()
        assert conn == "conn"
        pool.release(conn)


class _FakeChecker:
    def __init__(self):
        self.acls: dict[tuple[str, str], object] = {}
        self.deleted: list[tuple[str, str]] = []

    def set_acl(self, acl):
        self.acls[(acl.dataset, str(acl.role))] = acl

    def get_acl(self, dataset, role):
        return self.acls.get((dataset, str(role)))

    def delete_acl(self, dataset, role):
        key = (dataset, str(role))
        if key in self.acls:
            del self.acls[key]
            self.deleted.append(key)
            return True
        return False


class _FakeConfig:
    def __init__(self, rules):
        self.tag_access_rules = rules
        self.uri = "http://gravitino"
        self.metalake = "ml"
        self.lance_catalog_name = "cat"
        self.lance_schema_name = "sch"


class TestTagACLRecovery:
    def _resolver(self, checker, rules=None):
        from arrow_lake.catalog.tag_acl_resolver import TagAwareACLResolver

        return TagAwareACLResolver(_FakeConfig(rules or {"pii": {"visible_to": ["admin"]}}), checker)

    def test_tag_removed_recovers_visibility(self, monkeypatch):
        """H12: tag→untag must lift the column restriction, not leave the
        ACL frozen forever."""
        checker = _FakeChecker()
        r = self._resolver(checker)

        schema = [{"name": "id"}, {"name": "phone"}]

        def tags_with(tagged: dict | None):
            def fetch(table):
                return tagged
            return fetch

        monkeypatch.setattr(r, "_list_gravitino_tables", lambda: ["ds"])
        monkeypatch.setattr(r, "_get_table_schema", lambda t: schema)

        # round 1: phone tagged pii → viewer ACL hides phone
        monkeypatch.setattr(r, "_fetch_column_tags", lambda t: {"phone": ["pii"]})
        assert r.sync_tags_to_acls() == 2  # editor + viewer restricted
        assert checker.get_acl("ds", "viewer") is not None

        # round 2: tag lifted → previous ACL must be REMOVED
        monkeypatch.setattr(r, "_fetch_column_tags", lambda t: {})
        r.sync_tags_to_acls()
        assert ("ds", "viewer") in checker.deleted
        assert checker.get_acl("ds", "viewer") is None

    def test_fetch_failure_keeps_restrictions(self, monkeypatch):
        """H12: a failed tag fetch must skip the round — treating it as
        'no tags' would strip protections on a transient Gravitino error."""
        checker = _FakeChecker()
        r = self._resolver(checker)
        monkeypatch.setattr(r, "_list_gravitino_tables", lambda: ["ds"])
        monkeypatch.setattr(r, "_get_table_schema", lambda t: [{"name": "id"}, {"name": "phone"}])

        monkeypatch.setattr(r, "_fetch_column_tags", lambda t: {"phone": ["pii"]})
        r.sync_tags_to_acls()
        assert checker.get_acl("ds", "viewer") is not None

        # now the fetch itself fails (service down) → distinguishable miss
        def broken(_t):
            raise ConnectionError("gravitino down")

        monkeypatch.setattr(r, "_fetch_column_tags", broken)
        r.sync_tags_to_acls()
        assert ("ds", "viewer") not in checker.deleted
        assert checker.get_acl("ds", "viewer") is not None


class TestEmbedGuardReadsRedisMirror:
    def test_guard_consults_redis_mirror(self):
        """Worker B's de-dup guard must see worker A's running backfill via
        the Redis mirror, not just its process-local dict."""
        from pathlib import Path

        src = (Path(__file__).resolve().parents[3] / "arrow_lake/_lake_ingest.py").read_text()
        i = src.index("already_running = cur is not None")
        window = src[i : i + 700]
        assert "_read_embed_status_redis" in window, (
            "embed backfill guard ignores the Redis mirror — cross-worker "
            "duplicate backfills race"
        )
