"""W2 #4(v1.11.5)— DatasetClassificationStore(V026)。

契约:四档封闭集(越界 ValueError);roundtrip;更新换档保 created_at;
删除;未分级 get→None;重启持久(commit 纪律,文件库)。
"""

from __future__ import annotations

import pytest
from arrow_lake.system_db import Migrator, SystemDB
from arrow_lake.system_db.stores.classification import TIERS, DatasetClassificationStore


@pytest.fixture
def db() -> SystemDB:
    conn = SystemDB(":memory:")
    Migrator(conn).run()
    yield conn
    conn.close()


@pytest.fixture
def store(db: SystemDB) -> DatasetClassificationStore:
    return DatasetClassificationStore(db)


def test_tiers_closed_set() -> None:
    assert TIERS == ("public", "internal", "confidential", "restricted")


def test_roundtrip_and_missing(store: DatasetClassificationStore) -> None:
    rec = store.set("alerts", "internal", actor="op", note="试点首分级")
    assert rec["tier"] == "internal" and rec["actor"] == "op" and rec["note"] == "试点首分级"
    got = store.get("alerts")
    assert got is not None and got["tier"] == "internal"
    assert store.get("ghost") is None


def test_update_changes_tier_keeps_created(store: DatasetClassificationStore) -> None:
    store.set("alerts", "internal")
    rec2 = store.set("alerts", "restricted", actor="op2")
    assert rec2["tier"] == "restricted" and rec2["actor"] == "op2"
    assert rec2["created_at"] == store.get("alerts")["created_at"]  # upsert 保首登时间


def test_invalid_tier_rejected(store: DatasetClassificationStore) -> None:
    with pytest.raises(ValueError, match="tier must be one of"):
        store.set("alerts", "secret")


def test_delete(store: DatasetClassificationStore) -> None:
    store.set("alerts", "public")
    assert store.delete("alerts") is True
    assert store.get("alerts") is None
    assert store.delete("alerts") is False


def test_list_all(store: DatasetClassificationStore) -> None:
    store.set("b", "public")
    store.set("a", "restricted")
    rows = store.list_all()
    assert [r["dataset"] for r in rows] == ["a", "b"]
    assert rows[0]["tier"] == "restricted"


def test_restart_persistence(tmp_path) -> None:
    from arrow_lake.system_db import Migrator, SystemDB
    from arrow_lake.system_db.stores.classification import DatasetClassificationStore

    path = str(tmp_path / "sys.db")
    db1 = SystemDB(f"file:{path}")
    Migrator(db1).run()
    DatasetClassificationStore(db1).set("alerts", "confidential", actor="op")
    db1.close()

    db2 = SystemDB(f"file:{path}")
    Migrator(db2).run()
    rec = DatasetClassificationStore(db2).get("alerts")
    assert rec is not None and rec["tier"] == "confidential"
    db2.close()
