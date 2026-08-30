"""W1.4 — QualityReportStore(sys_quality_reports,V020)。"""

from __future__ import annotations

import pytest
from arrow_lake.system_db import Migrator, SystemDB
from arrow_lake.system_db.stores.quality_reports import QualityReportStore


@pytest.fixture
def db() -> SystemDB:
    conn = SystemDB(":memory:")
    Migrator(conn).run()
    yield conn
    conn.close()


def _payload(total: float, star: int = 4, admission: str = "bronze") -> dict:
    return {
        "total_score": total,
        "star": star,
        "admission": admission,
        "verdict": "pass",
        "dimensions": {"accuracy": {"score": 85.0, "details": {}, "source": "adl"}},
        "vetoes": [],
        "degraded": ["relevance"],
        "spec": {"weights": {"accuracy": 0.35}, "critical": False},
        "assessed_by": "sysop",
    }


def test_create_and_latest_roundtrip(db: SystemDB) -> None:
    store = QualityReportStore(db)
    rec = store.create_report("alerts", **_payload(85.25))
    assert rec["dataset"] == "alerts"
    assert rec["total_score"] == 85.25 and rec["star"] == 4
    assert rec["dimensions"]["accuracy"]["score"] == 85.0
    assert rec["degraded"] == ["relevance"]
    assert rec["assessed_by"] == "sysop"
    # JSON 复杂结构 roundtrip
    assert rec["spec"]["weights"] == {"accuracy": 0.35}


def test_history_newest_first_and_latest(db: SystemDB) -> None:
    store = QualityReportStore(db)
    store.create_report("alerts", **_payload(85.0))
    store.create_report("alerts", **_payload(90.0))
    store.create_report("alerts", **_payload(88.0))
    history = store.list_reports("alerts")
    assert [r["total_score"] for r in history] == [88.0, 90.0, 85.0]
    assert store.latest_report("alerts")["total_score"] == 88.0


def test_none_total_score_persists(db: SystemDB) -> None:
    store = QualityReportStore(db)
    store.create_report(
        "empty_ds", total_score=None, star=0, admission="none",
        verdict="degraded", dimensions={}, vetoes=[], degraded=[
            "relevance", "accuracy", "completeness", "diversity", "timeliness"],
        spec={},
    )
    latest = store.latest_report("empty_ds")
    assert latest is not None and latest["total_score"] is None
    assert latest["star"] == 0


def test_limit_and_missing_dataset(db: SystemDB) -> None:
    store = QualityReportStore(db)
    for i in range(5):
        store.create_report("alerts", **_payload(float(i)))
    assert len(store.list_reports("alerts", limit=3)) == 3
    assert store.list_reports("ghost") == []
    assert store.latest_report("ghost") is None


def test_migration_count_bumped() -> None:
    """迁移序号断言同步(项目约定:test_system_db 对齐)。"""
    from pathlib import Path
    migrations = Path("arrow_lake/system_db/migrations").glob("V*.sql")
    assert len(list(migrations)) == 21  # V020 reports + V022 drift (MS5)
