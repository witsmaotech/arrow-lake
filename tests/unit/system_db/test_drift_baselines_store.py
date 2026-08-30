"""W2.2 — DriftBaselineStore(sys_drift_baselines,V022)+ W2.c drift_kl。"""

from __future__ import annotations

import pytest
from arrow_lake.contract.schema import QualitySpec, parse_contract
from arrow_lake.quality.drift import DEFAULT_DRIFT_KL
from arrow_lake.quality.spec import resolve_quality_spec
from arrow_lake.system_db import Migrator, SystemDB
from arrow_lake.system_db.stores.drift_baselines import DriftBaselineStore


@pytest.fixture
def db() -> SystemDB:
    conn = SystemDB(":memory:")
    Migrator(conn).run()
    yield conn
    conn.close()


_SNAP = {
    "severity": {"kind": "categorical", "values": {"high": 6, "low": 4},
                 "other": 0, "total": 10},
}


def test_set_and_get_baseline_roundtrip(db: SystemDB) -> None:
    store = DriftBaselineStore(db)
    rec = store.set_baseline("alerts", _SNAP, source="release")
    assert rec["dataset"] == "alerts" and rec["source"] == "release"
    assert rec["columns"]["severity"]["values"]["high"] == 6
    latest = store.get_baseline("alerts")
    assert latest is not None and latest["id"] == rec["id"]


def test_latest_wins_and_history(db: SystemDB) -> None:
    store = DriftBaselineStore(db)
    store.set_baseline("alerts", _SNAP)
    store.set_baseline("alerts", {"x": {"kind": "numeric", "min": 0.0,
                                        "max": 1.0, "bins": 32,
                                        "counts": [1] * 32}}, source="release")
    history = store.list_history("alerts")
    assert len(history) == 2
    assert history[0]["source"] == "release"  # newest first
    assert store.get_baseline("alerts")["id"] == history[0]["id"]


def test_missing_dataset_none(db: SystemDB) -> None:
    store = DriftBaselineStore(db)
    assert store.get_baseline("ghost") is None
    assert store.list_history("ghost") == []


def test_migration_count_21() -> None:
    from pathlib import Path
    n = len(list(Path("arrow_lake/system_db/migrations").glob("V*.sql")))
    assert n == 21  # V022 加入后


# --- W2.c 契约 quality.drift_kl 覆盖 -----------------------------------------

def test_drift_kl_default() -> None:
    assert resolve_quality_spec(None).drift_kl == DEFAULT_DRIFT_KL == 0.1


def test_drift_kl_override_and_invalid() -> None:
    contract = parse_contract(
        "dataset: alerts\ntables:\n  alerts:\n    columns: []\n"
        "quality:\n  drift_kl: 0.05\n")
    assert contract.quality is not None
    assert resolve_quality_spec(contract.quality).drift_kl == 0.05
    with pytest.raises(ValueError):
        QualitySpec(drift_kl=0.0)   # 非正
    with pytest.raises(ValueError):
        QualitySpec(drift_kl=11.0)  # 越上界
