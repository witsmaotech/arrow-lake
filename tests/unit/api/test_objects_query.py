"""W4.2/W4.3 — /api/v1/objects/query(RBAC 接线 + 聚合 enrichment)。

安全关键契约(实施计划 W4.2):
* dataset 读权 403 / 表级 deny 双查 403 / 行过滤与列 ACL 经 enforce_sql_acl
  生效于**组装出的 SQL**(接线审计:captured SQL 必须含注入谓词);
* 无契约 422(S8)/ 未知 object_type 422 / 未知列与非法 op 422;
* 聚合:标识双路径(契约 pattern 直取 / entity_map 映射)、_links 带基数、
  _rules 只 active、_kg 匹配 miss 容忍。
"""

from __future__ import annotations

from types import SimpleNamespace

import pyarrow as pa
import pytest
from arrow_lake.api.auth_models import Role, TokenPayload
from arrow_lake.system_db import Migrator, SystemDB
from arrow_lake.system_db.stores.contracts import ContractStore
from arrow_lake.system_db.stores.entity_map import EntityMapStore
from arrow_lake.system_db.stores.ontology import OntologyRulesStore
from arrow_lake.system_db.stores.semantic_alignments import SemanticAlignmentStore
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

CONTRACT_YAML = """
dataset: gas_net
tables:
  segments:
    lifecycle: {column: 状态, states: [在建, 在运, 报废], initial: 在建}
    identifier:
      column: seg_id
      pattern: "GAS.SEGMENT.{区域}.{序列}"
    columns:
      - {name: 压力, label: 管段运行压力, unit: kPa}
      - {name: 材质, enum: [PE, 钢管]}
  src_b:
    columns: [{name: 压力}]
references:
  - {from: segments.station_id, to: stations.id, cardinality: N:1, kind: association}
"""

RESULT_TABLE = pa.table({
    "seg_id": ["GAS.SEGMENT.RG01.S047", "GAS.SEGMENT.RG01.S048"],
    "压力": [2000.0, 500.0],
    "材质": ["PE", "钢管"],
    "状态": ["在运", "在建"],
    "station_id": ["ST-01", "ST-02"],
})

SRC_B_TABLE = pa.table({
    "压力": [1.5],
    "本地编号": ["S-047"],
})


class StubLake:
    def __init__(self, tables: dict[str, pa.Table], container=True, graph=None):
        self._tables = tables
        self._container = container
        self._graph = graph
        self.captured: list[tuple[str, str]] = []

    def _get_storage(self):
        return SimpleNamespace(list_container_tables=lambda n: (
            ["segments", "src_b"] if self._container else []))

    def open_dataset(self, name, table=None):
        key = table or name
        return SimpleNamespace(schema=self._tables[key].schema)

    def olap_query(self, target, sql, max_rows=None):
        self.captured.append((target, sql))
        return SimpleNamespace(table=self._tables[target.split(".")[-1]], sql=sql)

    async def kg_get_graph(self, dataset, limit=300):
        return self._graph or {"nodes": [], "edges": [], "vertex_count": 0,
                               "edge_count": 0, "truncated": False}


class StubChecker:
    def __init__(self, acls=None, allowed=True):
        self.acls = acls or {}
        self.allowed = allowed
        self.access_calls: list[str] = []

    def get_acl(self, dataset, role):
        spec = self.acls.get(dataset.lower())
        if spec is None:
            return None
        # enforce_sql_acl 读取 DatasetACL 的 dataset/row_filter/visible_columns
        # 字段(违规消息用 .dataset);None visible 保真为空列表。
        return SimpleNamespace(
            dataset=dataset,
            row_filter=spec.row_filter,
            visible_columns=list(spec.visible_columns or []),
            denied_actions=spec.denied_actions,
        )

    def check_dataset_access(self, *, role, dataset, action, permissions=None):
        self.access_calls.append(f"{dataset}:{action}")
        return self.allowed

    def apply_table_filter(self, table, dataset, role):
        return table


def _acl(row_filter=None, visible=None, denied=frozenset()):
    return SimpleNamespace(row_filter=row_filter, visible_columns=visible,
                           denied_actions=denied)


@pytest.fixture
def db() -> SystemDB:
    conn = SystemDB(":memory:")
    Migrator(conn).run()
    ContractStore(conn).save_contract("gas_net", CONTRACT_YAML)
    rules = OntologyRulesStore(conn)
    rules.upsert_rule("GAS.R1", scope="gas_net", condition_expr="c",
                      conclusion="泄漏预警", source_ref="gb", rule_type="risk_control")
    rules.transition("GAS.R1", "active")
    rules.upsert_rule("GAS.R2", scope="*", condition_expr="c",
                      conclusion="全局规则(草稿不出现)", source_ref="s")
    yield conn
    conn.close()


def _client(db: SystemDB, *, lake, checker=None, role=Role.VIEWER) -> TestClient:
    from arrow_lake.api.routers.objects import router

    app = FastAPI()
    app.state.lake = lake
    app.state.checker = checker or StubChecker()
    app.state.contract_store = ContractStore(db)
    app.state.semantic_alignment_store = SemanticAlignmentStore(db)
    app.state.ontology_rules_store = OntologyRulesStore(db)
    app.state.entity_map_store = EntityMapStore(db)

    @app.middleware("http")
    async def _inject_user(request: Request, call_next):
        request.state.user = TokenPayload(sub="tester", role=role, exp=0, iat=0)
        return await call_next(request)

    app.include_router(router)
    return TestClient(app)


def _body(**overrides) -> dict:
    payload = {"dataset": "gas_net", "object_type": "segments"}
    payload.update(overrides)
    return payload


class TestHappyPath:
    def test_query_returns_objects(self, db: SystemDB) -> None:
        lake = StubLake({"segments": RESULT_TABLE, "src_b": SRC_B_TABLE})
        with _client(db, lake=lake) as c:
            r = c.post("/api/v1/objects/query", json=_body(include_rules=True))
        assert r.status_code == 200, r.text
        data = r.json()
        assert data["object_type"] == "segments"
        assert len(data["objects"]) == 2
        o = data["objects"][0]
        assert o["object_id"] == "GAS.SEGMENT.RG01.S047"
        assert o["identifier"]["matched"] is True
        assert o["identifier"]["components"] == {"区域": "RG01", "序列": "S047"}
        assert o["lifecycle_state"] == "在运"
        assert o["attributes"]["压力"] == 2000.0
        link = o["_links"][0]
        assert link["to_table"] == "stations" and link["value"] == "ST-01"
        assert link["cardinality"] == "N:1" and link["kind"] == "association"
        # rules: only ACTIVE rules for scope + '*' (draft invisible)
        assert [x["rule_id"] for x in data["_rules"]] == ["GAS.R1"]
        assert data["_rules"][0]["rule_type"] == "risk_control"
        # column meta carries contract labels/units
        cols = {x["name"]: x for x in data["columns"]}
        assert cols["压力"]["label"] == "管段运行压力"
        assert cols["压力"]["unit"] == "kPa"
        # executed against the two-part target
        assert lake.captured[0][0] == "gas_net.segments"

    def test_alignment_meta_present(self, db: SystemDB) -> None:
        SemanticAlignmentStore(db).save_alignment("gas_net", """
dataset: gas_net
tables:
  segments:
    columns:
      压力: {unit: {from: MPa, to: kPa}}
""")
        lake = StubLake({"segments": RESULT_TABLE, "src_b": SRC_B_TABLE})
        with _client(db, lake=lake) as c:
            r = c.post("/api/v1/objects/query", json=_body())
        assert r.status_code == 200
        assert r.json()["aligned"]["压力"] == {"kind": "unit", "from": "MPa", "to": "kPa"}
        assert "* 1000.0" in lake.captured[0][1]

    def test_single_table_dataset(self, db: SystemDB) -> None:
        ContractStore(db).save_contract("solo", """
dataset: solo
tables:
  solo:
    columns: [{name: 压力}]
""")
        tbl = pa.table({"压力": [1.0]})
        lake = StubLake({"solo": tbl}, container=False)
        with _client(db, lake=lake) as c:
            r = c.post("/api/v1/objects/query",
                       json={"dataset": "solo", "object_type": "solo"})
        assert r.status_code == 200, r.text
        assert lake.captured[0][0] == "solo"

    def test_types_endpoint(self, db: SystemDB) -> None:
        lake = StubLake({"segments": RESULT_TABLE, "src_b": SRC_B_TABLE})
        with _client(db, lake=lake) as c:
            r = c.get("/api/v1/objects/types?dataset=gas_net")
            r2 = c.get("/api/v1/objects/types?dataset=unknown_ds")
        assert r.status_code == 200
        types = {t["table"]: t for t in r.json()["types"]}
        assert types["segments"]["object_class"] is None
        assert types["segments"]["lifecycle"]["states"] == ["在建", "在运", "报废"]
        assert types["segments"]["identifier_column"] == "seg_id"
        assert r2.json() == {"dataset": "unknown_ds", "has_contract": False, "types": []}

    def test_types_denied_403(self, db: SystemDB) -> None:
        """F1(review): deny 用户不得读契约结构。"""
        lake = StubLake({"segments": RESULT_TABLE, "src_b": SRC_B_TABLE})
        with _client(db, lake=lake, checker=StubChecker(allowed=False)) as c:
            r = c.get("/api/v1/objects/types?dataset=gas_net")
        assert r.status_code == 403

    def test_viewer_rules_stripped_and_pre_enforcement_sql(self, db: SystemDB) -> None:
        """F10(review): VIEWER 不见 condition_expr/source_ref;SQL 回带 enforce 前。"""
        lake = StubLake({"segments": RESULT_TABLE, "src_b": SRC_B_TABLE})
        with _client(db, lake=lake) as c:
            r = c.post("/api/v1/objects/query", json=_body())
        data = r.json()
        rule = data["_rules"][0]
        assert "condition_expr" not in rule and "source_ref" not in rule
        assert rule["rule_id"] == "GAS.R1"
        assert "FROM \"gas_net\".\"segments\"" in data["sql"]  # pre-enforcement

    def test_id_column_unknown_422(self, db: SystemDB) -> None:
        lake = StubLake({"segments": RESULT_TABLE, "src_b": SRC_B_TABLE})
        with _client(db, lake=lake) as c:
            r = c.post("/api/v1/objects/query",
                       json={"dataset": "gas_net", "object_type": "src_b",
                             "id_column": "ghost"})
        assert r.status_code == 422


class TestValidation:
    def test_no_contract_422(self, db: SystemDB) -> None:
        lake = StubLake({"segments": RESULT_TABLE})
        with _client(db, lake=lake) as c:
            r = c.post("/api/v1/objects/query",
                       json={"dataset": "unknown_ds", "object_type": "x"})
        assert r.status_code == 422

    def test_unknown_object_type_422(self, db: SystemDB) -> None:
        lake = StubLake({"segments": RESULT_TABLE, "src_b": SRC_B_TABLE})
        with _client(db, lake=lake) as c:
            r = c.post("/api/v1/objects/query", json=_body(object_type="ghost"))
        assert r.status_code == 422

    def test_unknown_filter_column_422(self, db: SystemDB) -> None:
        lake = StubLake({"segments": RESULT_TABLE, "src_b": SRC_B_TABLE})
        with _client(db, lake=lake) as c:
            r = c.post("/api/v1/objects/query", json=_body(
                filter=[{"column": "ghost", "op": "eq", "value": 1}]))
        assert r.status_code == 422

    def test_limit_bounds_422(self, db: SystemDB) -> None:
        lake = StubLake({"segments": RESULT_TABLE, "src_b": SRC_B_TABLE})
        with _client(db, lake=lake) as c:
            assert c.post("/api/v1/objects/query",
                          json=_body(limit=501)).status_code == 422


class TestRbacWiring:
    def test_dataset_read_denied_403(self, db: SystemDB) -> None:
        lake = StubLake({"segments": RESULT_TABLE, "src_b": SRC_B_TABLE})
        with _client(db, lake=lake, checker=StubChecker(allowed=False)) as c:
            r = c.post("/api/v1/objects/query", json=_body())
        assert r.status_code == 403

    def test_table_level_deny_403(self, db: SystemDB) -> None:
        lake = StubLake({"segments": RESULT_TABLE, "src_b": SRC_B_TABLE})
        checker = StubChecker(acls={
            "gas_net.segments": _acl(denied=frozenset({"read"})),
        })
        with _client(db, lake=lake, checker=checker) as c:
            r = c.post("/api/v1/objects/query", json=_body())
        assert r.status_code == 403

    def test_row_filter_injected_into_composed_sql(self, db: SystemDB) -> None:
        """接线审计:enforce_sql_acl 必须作用于对象查询组装的 SQL。"""
        lake = StubLake({"segments": RESULT_TABLE, "src_b": SRC_B_TABLE})
        checker = StubChecker(acls={
            "gas_net.segments": _acl(row_filter='"压力" > 100'),
        })
        with _client(db, lake=lake, checker=checker) as c:
            r = c.post("/api/v1/objects/query", json=_body())
        assert r.status_code == 200
        assert "WHERE" in lake.captured[0][1]
        assert '"压力" > 100' in lake.captured[0][1]

    def test_visible_columns_restriction_422(self, db: SystemDB) -> None:
        """列受限用户:组装 SQL 引用隐藏列 → enforce_sql_acl 拒(422)。"""
        lake = StubLake({"segments": RESULT_TABLE, "src_b": SRC_B_TABLE})
        checker = StubChecker(acls={
            "gas_net.segments": _acl(visible=["seg_id"]),
        })
        with _client(db, lake=lake, checker=checker) as c:
            r = c.post("/api/v1/objects/query", json=_body())
        assert r.status_code == 422


class TestEnrichment:
    def test_identifier_fallback_via_entity_map(self, db: SystemDB) -> None:
        em = EntityMapStore(db)
        em.upsert(scope="gas_net", table_name="src_b", source_system="GIS-B",
                  source_id="S-047", object_id="GAS.SEGMENT.RG01.S047")
        lake = StubLake({"segments": RESULT_TABLE, "src_b": SRC_B_TABLE})
        with _client(db, lake=lake) as c:
            r = c.post("/api/v1/objects/query",
                       json={"dataset": "gas_net", "object_type": "src_b",
                             "id_column": "本地编号"})
        assert r.status_code == 200, r.text
        o = r.json()["objects"][0]
        assert o["object_id"] == "GAS.SEGMENT.RG01.S047"
        assert o["identifier"]["matched"] is False
        assert o["identifier"]["mapped"] is True

    def test_ambiguous_mapping_keeps_source_id(self, db: SystemDB) -> None:
        em = EntityMapStore(db)
        em.upsert(scope="gas_net", table_name="src_b", source_system="A",
                  source_id="S-047", object_id="OBJ-1")
        em.upsert(scope="gas_net", table_name="src_b", source_system="B",
                  source_id="S-047", object_id="OBJ-2")
        lake = StubLake({"segments": RESULT_TABLE, "src_b": SRC_B_TABLE})
        with _client(db, lake=lake) as c:
            r = c.post("/api/v1/objects/query",
                       json={"dataset": "gas_net", "object_type": "src_b",
                             "id_column": "本地编号"})
        o = r.json()["objects"][0]
        assert o["object_id"] == "S-047"
        assert o["identifier"]["mapped"] is False

    def test_kg_match_and_miss_tolerance(self, db: SystemDB) -> None:
        graph = {
            "nodes": [
                {"id": "v1", "name": "gas.segment.rg01.s047", "type": "设备",
                 "label": "管段", "definition": ""},
                {"id": "v2", "name": "调压站", "type": "场站", "label": "场站",
                 "definition": ""},
            ],
            "edges": [{"id": "e1", "source": "v1", "target": "v2",
                       "label": "related_to", "relation_type": "连接"}],
        }
        lake = StubLake({"segments": RESULT_TABLE, "src_b": SRC_B_TABLE},
                        graph=graph)
        with _client(db, lake=lake) as c:
            r = c.post("/api/v1/objects/query", json=_body(include_kg=True))
        objs = r.json()["objects"]
        assert objs[0]["_kg"]["matched"] is True
        assert objs[0]["_kg"]["vertex"]["name"] == "gas.segment.rg01.s047"
        assert objs[0]["_kg"]["neighbors"][0]["name"] == "调压站"
        assert objs[1]["_kg"]["matched"] is False  # miss tolerated

    def test_kg_error_tolerated(self, db: SystemDB) -> None:
        class KgBoomLake(StubLake):
            async def kg_get_graph(self, dataset, limit=300):
                raise RuntimeError("kg down")
        lake = KgBoomLake({"segments": RESULT_TABLE, "src_b": SRC_B_TABLE})
        with _client(db, lake=lake) as c:
            r = c.post("/api/v1/objects/query", json=_body(include_kg=True))
        assert r.status_code == 200
        assert r.json()["objects"][0]["_kg"]["matched"] is False
