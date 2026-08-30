"""W3.1 — 发布注册:tag 语义 + V021 sys_releases + ReleaseStore(MS5 W3)。

语义化版本(S8):schema 破坏=MAJOR / 数据增量=MINOR / 质量修订=PATCH,
人工指定默认 MINOR;首个发布 v1.0.0。重复 tag 拒(UNIQUE 约束);
retire=软状态(历史保留);劣化比较基准 = 最新 **active** 发布。
"""

from __future__ import annotations

import pytest
from arrow_lake.release.registry import (
    format_tag,
    next_tag,
    parse_tag,
)
from arrow_lake.system_db import Migrator, SystemDB
from arrow_lake.system_db.stores.releases import ReleaseStore


@pytest.fixture
def db() -> SystemDB:
    conn = SystemDB(":memory:")
    Migrator(conn).run()
    yield conn
    conn.close()


# --- 纯函数:tag 语义 ---------------------------------------------------------

def test_parse_and_format_roundtrip() -> None:
    assert parse_tag("v1.2.3") == (1, 2, 3)
    assert format_tag((1, 2, 3)) == "v1.2.3"
    for bad in ("1.2.3", "v1.2", "v1.2.x", "v0.1.0", "v1.2.3.4", ""):
        with pytest.raises(ValueError):
            parse_tag(bad)


def test_next_tag_first_release_is_v1_0_0() -> None:
    for bump in ("major", "minor", "patch"):
        assert next_tag(None, bump=bump) == (1, 0, 0)


def test_next_tag_bump_levels() -> None:
    assert next_tag((1, 2, 3), bump="major") == (2, 0, 0)
    assert next_tag((1, 2, 3), bump="minor") == (1, 3, 0)   # 默认
    assert next_tag((1, 2, 3), bump="patch") == (1, 2, 4)
    assert next_tag((1, 2, 3)) == (1, 3, 0)


def test_next_tag_zero_floor() -> None:
    # (0,x,y) 不应出现(parse 拒 v0),但防御性:patch 溢出进位
    assert next_tag((1, 0, 0), bump="patch") == (1, 0, 1)


# --- store -------------------------------------------------------------------

def _rec(tag: str = "v1.0.0", **kw) -> dict:
    base = dict(
        dataset="alerts", tag=tag, lance_version=7, changelog="first",
        quality_report_id=1, total_score=92.5, star=4, admission="silver",
        datasheet_yaml="id: alerts\n", released_by="sysop",
    )
    base.update(kw)
    return base


def test_create_and_get_roundtrip(db: SystemDB) -> None:
    store = ReleaseStore(db)
    rec = store.create_release(**_rec())
    assert rec["tag"] == "v1.0.0" and rec["lance_version"] == 7
    assert rec["major"] == 1 and rec["minor"] == 0 and rec["patch"] == 0
    assert rec["status"] == "active"
    got = store.get_release("alerts", "v1.0.0")
    assert got is not None and got["total_score"] == 92.5


def test_duplicate_tag_rejected(db: SystemDB) -> None:
    store = ReleaseStore(db)
    assert store.create_release(**_rec()) is not None
    assert store.create_release(**_rec()) is None  # UNIQUE(dataset, tag)


def test_history_newest_first_and_latest_active(db: SystemDB) -> None:
    store = ReleaseStore(db)
    store.create_release(**_rec("v1.0.0", total_score=90.0))
    store.create_release(**_rec("v1.1.0", total_score=91.0))
    store.create_release(**_rec("v1.2.0", total_score=92.0))
    history = store.list_releases("alerts")
    assert [r["tag"] for r in history] == ["v1.2.0", "v1.1.0", "v1.0.0"]
    assert store.latest_release("alerts")["tag"] == "v1.2.0"


def test_retire_soft_state_and_latest_active(db: SystemDB) -> None:
    store = ReleaseStore(db)
    store.create_release(**_rec("v1.0.0", total_score=90.0))
    store.create_release(**_rec("v1.1.0", total_score=85.0))
    # 退役最新 → latest active 回落 v1.0.0;历史仍含全部
    assert store.retire_release("alerts", "v1.1.0") is True
    assert store.retire_release("alerts", "v1.1.0") is False  # 幂等
    assert store.latest_release("alerts")["tag"] == "v1.0.0"
    assert len(store.list_releases("alerts")) == 2
    got = store.get_release("alerts", "v1.1.0")
    assert got is not None and got["status"] == "retired"


def test_missing_returns_none(db: SystemDB) -> None:
    store = ReleaseStore(db)
    assert store.get_release("alerts", "v9.9.9") is None
    assert store.latest_release("ghost") is None
    assert store.list_releases("ghost") == []


def test_migration_count_22() -> None:
    from pathlib import Path
    n = len(list(Path("arrow_lake/system_db/migrations").glob("V*.sql")))
    assert n == 22  # V021 releases 加入后(V020/V022 已在 W1/W2)
