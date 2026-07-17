"""Tests for the v1.9.0 review perf fixes.

P1: validate_token throttles last_used_at writes (helper under test).
P2: _lineage_after_ingest records asynchronously via a bounded queue + worker.
"""

from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone

from arrow_lake._lake_lineage import _LakeLineageMixin
from arrow_lake.api.rbac import _ROLE_PERMISSIONS
from arrow_lake.system_db import Migrator, SystemDB
from arrow_lake.system_db.stores import RbacStore
from arrow_lake.system_db.stores.identity import (
    LAST_USED_THROTTLE_SECONDS,
    _should_update_last_used,
)


class TestLastUsedThrottle:
    def test_missing_or_none_triggers_write(self) -> None:
        assert _should_update_last_used(None) is True
        assert _should_update_last_used("") is True

    def test_recent_skips_write(self) -> None:
        recent = datetime.now(timezone.utc).isoformat()
        assert _should_update_last_used(recent) is False

    def test_stale_triggers_write(self) -> None:
        old = (datetime.now(timezone.utc) - timedelta(seconds=LAST_USED_THROTTLE_SECONDS + 30)).isoformat()
        assert _should_update_last_used(old) is True

    def test_naive_sqlite_timestamp_handled(self) -> None:
        # datetime('now') format: "YYYY-MM-DD HH:MM:SS" (naive UTC)
        recent = datetime.now(timezone.utc).replace(tzinfo=None).isoformat(sep=" ")
        assert _should_update_last_used(recent) is False


class TestAsyncLineageWorker:
    def test_enqueue_drains_off_thread(self) -> None:
        class _L(_LakeLineageMixin):
            def __init__(self) -> None:
                self.recorded: list[tuple] = []

            def lineage_record_event(self, dn, op, *, source_datasets=None,
                                     transform_type="", metadata=None) -> None:
                self.recorded.append((dn, op, transform_type, metadata))

        lake = _L()
        # non-blocking enqueue from the "request" thread
        lake._lineage_after_ingest("ds1", source_paths=["f.csv"], transform_type="ingest")
        lake._lineage_after_ingest("ds2", transform_type="create", operation="create")

        # wait for the daemon worker to drain (poll; Queue.join has no timeout)
        deadline = time.monotonic() + 2.0
        while len(lake.recorded) < 2 and time.monotonic() < deadline:
            time.sleep(0.005)

        assert len(lake.recorded) == 2
        names = {r[0] for r in lake.recorded}
        assert names == {"ds1", "ds2"}
        # metadata carries source_paths
        rec = next(r for r in lake.recorded if r[0] == "ds1")
        assert rec[3]["source_paths"] == ["f.csv"]


def _raise(*_a, **_k):
    raise RuntimeError("sqld down")


class TestRbacServeStale:
    def test_serves_last_known_on_store_error(self) -> None:
        db = SystemDB(":memory:")
        Migrator(db).run()
        store = RbacStore(db, cache_ttl=0.01, serve_stale=True)
        store.seed_role_permissions(_ROLE_PERMISSIONS)
        assert "admin:manage" in store.get_role_permissions("admin")
        time.sleep(0.02)  # TTL expires; entry lingers for get_stale
        store._db.execute = _raise  # simulate sqld outage
        stale = store.get_role_permissions("admin")
        assert "admin:manage" in stale  # served stale, not denied

    def test_strict_deny_when_never_cached(self) -> None:
        db = SystemDB(":memory:")
        Migrator(db).run()
        store = RbacStore(db, cache_ttl=60, serve_stale=True)
        store._db.execute = _raise  # outage before any read of 'ghost'
        assert store.get_role_permissions("ghost") == frozenset()  # deny

    def test_strict_fail_close_when_disabled(self) -> None:
        db = SystemDB(":memory:")
        Migrator(db).run()
        store = RbacStore(db, cache_ttl=60, serve_stale=False)
        store.get_role_permissions("admin")  # warm
        store._db.execute = _raise
        assert store.get_role_permissions("admin") == frozenset()  # deny, no stale

