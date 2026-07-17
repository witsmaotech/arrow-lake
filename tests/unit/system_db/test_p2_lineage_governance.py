"""P2 store tests (v1.9.0): LineageIndexStore + GovernanceStore."""

from __future__ import annotations

import pytest

from arrow_lake.system_db import Migrator, SystemDB
from arrow_lake.system_db.stores import GovernanceStore, LineageIndexStore


@pytest.fixture
def db() -> SystemDB:
    conn = SystemDB(":memory:")
    Migrator(conn).run()
    yield conn
    conn.close()


class TestLineageIndexStore:
    def test_fail_backfill_mode(self, db: SystemDB) -> None:
        assert LineageIndexStore(db).fail_mode == "fail_backfill"

    def test_index_upstream_downstream(self, db: SystemDB) -> None:
        idx = LineageIndexStore(db)
        # raw → clean → model : two events feeding "model"
        idx.index_event(event_id="e1", target="clean", sources=["raw"],
                        operation="transform", occurred_at="2026-07-17T01:00:00Z")
        idx.index_event(event_id="e2", target="model", sources=["clean"],
                        operation="train", occurred_at="2026-07-17T02:00:00Z")
        # upstream of "model" = clean ; downstream of "raw" = clean
        up = idx.upstream("model")
        assert [u["dataset"] for u in up] == ["clean"]
        down = idx.downstream("raw")
        assert [d["dataset"] for d in down] == ["clean"]
        n = idx.neighbors("clean")
        assert {x["dataset"] for x in n["upstream"]} == {"raw"}
        assert {x["dataset"] for x in n["downstream"]} == {"model"}
        assert idx.edge_count() == 2

    def test_idempotent_and_self_edges_skipped(self, db: SystemDB) -> None:
        idx = LineageIndexStore(db)
        idx.index_event(event_id="e1", target="d", sources=["s", "d", "s"],
                        occurred_at="t")
        # self-edge "d"→"d" skipped; duplicate "s" collapsed to one edge
        assert idx.edge_count() == 1
        # re-index same event_id → no new edge (UNIQUE)
        idx.index_event(event_id="e1", target="d", sources=["s"], occurred_at="t")
        assert idx.edge_count() == 1

    def test_clear(self, db: SystemDB) -> None:
        idx = LineageIndexStore(db)
        idx.index_event(event_id="e1", target="d", sources=["s"], occurred_at="t")
        assert idx.clear() == 1
        assert idx.edge_count() == 0


class TestGovernanceStore:
    def test_schema_changelog(self, db: SystemDB) -> None:
        g = GovernanceStore(db)
        g.record_schema_change("ds1", "add_column",
                               from_schema={"cols": ["a"]},
                               to_schema={"cols": ["a", "b"]},
                               details={"column": "b"}, actor="alice")
        rows = g.list_schema_changes("ds1")
        assert len(rows) == 1
        assert rows[0]["change_type"] == "add_column"
        assert rows[0]["to_schema"] == {"cols": ["a", "b"]}
        assert rows[0]["actor"] == "alice"

    def test_maintenance_runs(self, db: SystemDB) -> None:
        g = GovernanceStore(db)
        g.record_maintenance_run("vacuum", "ok", report={"freed": 1024})
        rows = g.list_maintenance_runs("vacuum")
        assert len(rows) == 1 and rows[0]["report"] == {"freed": 1024}

    def test_schedules_upsert_list_delete(self, db: SystemDB) -> None:
        g = GovernanceStore(db)
        g.upsert_schedule("nightly", "0 2 * * *", "backup", params={"keep": 7})
        g.upsert_schedule("nightly", "0 3 * * *", "backup", enabled=False)  # update
        scheds = g.list_schedules()
        assert len(scheds) == 1
        assert scheds[0]["cron_expr"] == "0 3 * * *" and scheds[0]["enabled"] is False
        assert g.list_schedules(enabled_only=True) == []
        assert g.delete_schedule("nightly") is True
        assert g.list_schedules() == []

    def test_config_changelog(self, db: SystemDB) -> None:
        g = GovernanceStore(db)
        g.record_config_change("api.api_key", old_value="old", new_value="new", actor="bob")
        rows = g.list_config_changes("api.api_key")
        assert len(rows) == 1 and rows[0]["new_value"] == "new"
