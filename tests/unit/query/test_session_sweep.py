"""M-6(v1.10.7 review,发版前清偿):会话归还兜底清扫。

主通道是逐查询 finally 清注册(P0-6);本测试钉住**归还空闲池**时的
兜底:漏网注册(temp 对象/桥接 schema)不随连接带给下一个借用者——
跨端点数据可见性红线。main schema 的用户对象(物化视图)不受影响。
"""

from __future__ import annotations

import duckdb
import pyarrow as pa
import pytest
from arrow_lake.config import OlapConfig
from arrow_lake.query.session_manager import DuckDBSessionManager


@pytest.fixture
def pool() -> DuckDBSessionManager:
    mgr = DuckDBSessionManager(OlapConfig())
    yield mgr


def _borrow(mgr: DuckDBSessionManager):
    return mgr.acquire()


def test_sweep_removes_leftover_temp_registrations(pool) -> None:
    """漏网的 conn.register(temp 对象)在归还时被清。"""
    secret = pa.table({"col": pa.array(["TOP-SECRET"], pa.string())})
    session = _borrow(pool)
    with session as conn:
        conn.register("secret_leftover", secret)  # 模拟漏网(无 finally 清)
    # 归还后再借同一个连接(空闲池复用)→ 注册物必须已消失
    with _borrow(pool) as conn2:
        with pytest.raises(duckdb.Error):
            conn2.execute("SELECT * FROM secret_leftover").fetchall()


def test_sweep_removes_leftover_bridge_schemas(pool) -> None:
    """两段容器注册残留的整 schema 在归还时被 CASCADE 清。"""
    session = _borrow(pool)
    with session as conn:
        conn.execute("CREATE SCHEMA ds_leftover")
        conn.execute('CREATE VIEW ds_leftover.tbl AS SELECT 42 AS x')
    with _borrow(pool) as conn2:
        with pytest.raises(duckdb.Error):
            conn2.execute("SELECT * FROM ds_leftover.tbl").fetchall()


def test_sweep_preserves_main_user_objects(pool) -> None:
    """main schema 的用户对象(物化视图形态)不被兜底误删。"""
    session = _borrow(pool)
    with session as conn:
        conn.execute("CREATE VIEW mv_user_asset AS SELECT 7 AS v")
    with _borrow(pool) as conn2:
        assert conn2.execute(
            "SELECT v FROM mv_user_asset").fetchone()[0] == 7
