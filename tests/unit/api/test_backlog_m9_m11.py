"""Backlog M-9/M-10/M-11(v1.11.2 清偿批)——信息泄露三修。

* M-9: X-SQL / meta.sql 回显用户**原始** SQL(不含 enforced 行过滤谓词值)
* M-10: graph_query src/dst/weight 列须在用户 visible_columns 内(422)
* M-11: gravitino /tables/{name} 与 column-tags 补 dataset deny 守卫
"""
from __future__ import annotations

from types import SimpleNamespace

import pyarrow as pa
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient


class _Checker:
    def __init__(self, *, visible=None, allowed=True):
        self._visible = visible
        self._allowed = allowed

    def get_acl(self, dataset, role):
        if self._visible is None:
            return None
        return SimpleNamespace(visible_columns=self._visible,
                               row_filter=None, denied_actions=frozenset())

    def check_dataset_access(self, *, role, dataset, action, permissions=None):
        return self._allowed

    def apply_table_filter(self, table, dataset, role):
        return table


_RESULT_TABLE = pa.table({"a": [1, 2, 3]})


class _FakeLake:
    def olap_query(self, target, sql, max_rows=None):
        return SimpleNamespace(table=_RESULT_TABLE, sql="SELECT a WHERE secret > 100")

    def sql_query(self, target, sql, max_rows=None):
        return SimpleNamespace(table=_RESULT_TABLE, sql="SELECT a WHERE secret > 100")

    def graph_query(self, target, **kw):
        return SimpleNamespace(table=_RESULT_TABLE, sql="WITH RECURSIVE ...")


def _app(checker):
    from arrow_lake.api.routers.query import router as query_router

    app = FastAPI()
    app.state.lake = _FakeLake()

    from arrow_lake.api.deps import get_checker, get_lake
    app.dependency_overrides[get_lake] = lambda: app.state.lake
    app.dependency_overrides[get_checker] = lambda: checker

    @app.middleware("http")
    async def _inject(request: Request, call_next):
        from arrow_lake.api.auth_models import Role, TokenPayload
        request.state.user = TokenPayload(sub="t", role=Role.EDITOR, exp=0, iat=0)
        return await call_next(request)

    app.include_router(query_router)
    return TestClient(app)


class TestM9:
    def test_sql_echo_returns_original_not_enforced(self):
        c = _app(_Checker())
        r = c.post("/api/v1/datasets/test_ds/query/olap",
                   json={"sql": "SELECT a FROM t"})
        assert r.status_code == 200, r.text
        assert r.json()["meta"]["sql"] == "SELECT a FROM t"  # 原始,非 enforced


class TestM10:
    def test_hidden_column_rejected_422(self):
        checker = _Checker(visible=["col_x", "col_y"])
        c = _app(checker)
        r = c.post("/api/v1/datasets/test_ds/query/graph",
                   json={"src_col": "col_secret", "dst_col": "col_x",
                         "start_node": "n1"})
        assert r.status_code == 422
        assert "col_secret" in r.json()["detail"]

    def test_visible_column_passes(self):
        checker = _Checker(visible=["col_x", "col_y"])
        c = _app(checker)
        r = c.post("/api/v1/datasets/test_ds/query/graph",
                   json={"src_col": "col_x", "dst_col": "col_y",
                         "start_node": "n1"})
        assert r.status_code == 200, r.text


class TestM11:
    def test_gravitino_table_endpoint_guards(self):
        """两个 VIEWER 元数据端点挂了 authorize_dataset_read 守卫(M-11)。"""
        import inspect

        from arrow_lake.api.routers.gravitino import (
            get_table,
            list_column_tags,
        )

        for fn in (get_table, list_column_tags):
            src = inspect.getsource(fn)
            assert "authorize_dataset_read" in src, fn.__name__
