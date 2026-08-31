"""v1.11.1 MS2 对象层 API — W2.3 entity-map + W4 Object Set 查询(F2.3)。

* entity-map(源系统 ID → 对象 ID):显式维护面(ADMIN),不挂摄入。
* ``GET  /api/v1/objects/types`` —— 契约表节 → 对象类列表(VIEWER)。
* ``POST /api/v1/objects/query`` —— 按对象类型+过滤返回**业务对象**
  (Lance 列+标识+关联+KG 子图+规则引用的聚合),VIEWER。

安全关键(W4.2):对象查询的 SQL 是服务端拼装的受限 SELECT,随后走
/query/olap 同一条安全路径——dataset 读权 → 表级 deny 双查 →
``validate_sql_safety`` → ``enforce_sql_acl``(行过滤/列 ACL)→ OLAP
执行器 → ``apply_table_filter``;接线审计测试钉住,不建旁路。
"""

from __future__ import annotations

import logging
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field

from arrow_lake.api.auth_models import Role
from arrow_lake.api.deps import audit_write, get_checker, get_lake, require_role
from arrow_lake.api.utils import run_sync

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/v1/objects", tags=["objects"])


def _store(request: Request):
    return getattr(request.app.state, "entity_map_store", None)


def _require_store(store, name: str):
    if store is None:
        raise HTTPException(
            status_code=503,
            detail=f"system_db disabled; {name} unavailable",
        )
    return store


# ---------------------------------------------------------------------------
# entity-map(显式维护)
# ---------------------------------------------------------------------------


class EntityMapping(BaseModel):
    source_system: str = Field(
        default="", max_length=200, description="源系统标识,如 SCADA-A / GIS-B"
    )
    source_id: str = Field(..., min_length=1, max_length=500, description="源系统本地 ID")
    object_id: str = Field(
        ..., min_length=1, max_length=500, description="规范对象 ID(契约 identifier 形态)"
    )


class EntityMapBulkRequest(BaseModel):
    scope: str = Field(..., min_length=1, max_length=200, description="dataset(容器)名")
    table: str = Field(..., min_length=1, max_length=200)
    mappings: list[EntityMapping] = Field(..., min_length=1, max_length=10_000)


@router.get("/entity-map", dependencies=[Depends(require_role(Role.ADMIN))])
async def list_entity_map(
    request: Request,
    scope: str = Query(description="dataset (container) name"),
    table: str | None = Query(default=None),
    limit: int = Query(default=500, ge=1, le=10_000),
) -> dict:
    """List entity-map entries for a scope (optionally one table)."""
    store = _require_store(_store(request), "entity map")
    items = store.list_entries(scope=scope, table_name=table, limit=limit)
    return {"success": True, "data": items, "count": len(items)}


@router.post("/entity-map", dependencies=[Depends(require_role(Role.ADMIN))])
async def bulk_upsert_entity_map(req: EntityMapBulkRequest, request: Request) -> dict:
    """Bulk upsert source-id → object-id mappings (idempotent)."""
    store = _require_store(_store(request), "entity map")
    written = store.bulk_upsert(
        [{"scope": req.scope, "table_name": req.table, **m.model_dump()} for m in req.mappings]
    )
    return {"success": True, "data": {"written": written, "scope": req.scope, "table": req.table}}


@router.delete("/entity-map", dependencies=[Depends(require_role(Role.ADMIN))])
async def delete_entity_map(
    request: Request,
    scope: str = Query(description="dataset (container) name"),
    table: str = Query(),
    source_system: str = Query(default=""),
    source_id: str = Query(),
    user=Depends(require_role(Role.ADMIN)),
) -> dict:
    """Delete one mapping by its four-part key."""
    store = _require_store(_store(request), "entity map")
    deleted = store.delete(
        scope=scope,
        table_name=table,
        source_system=source_system,
        source_id=source_id,
    )
    if not deleted:
        raise HTTPException(status_code=404, detail="entity mapping not found")
    audit_write(request, "semantic.entity_map_deleted", actor=user.sub,
                dataset=scope,
                payload={"table": table, "source_system": source_system,
                         "source_id": source_id})
    return {"success": True, "data": {"deleted": True}}


# ---------------------------------------------------------------------------
# Object Set(F2.3/W4)
# ---------------------------------------------------------------------------

_OBJ_TIMEOUT = 60
_KG_GRAPH_FETCH_LIMIT = 3000
_KG_NEIGHBOR_CAP = 10


def _norm(v: Any) -> str:
    return str(v).strip().casefold()


class ObjectFilter(BaseModel):
    column: str = Field(min_length=1, max_length=200)
    op: Literal["eq", "ne", "gt", "gte", "lt", "lte", "in", "like", "is_null", "is_not_null"]
    value: Any = None


class ObjectSetQueryRequest(BaseModel):
    dataset: str = Field(min_length=1, max_length=200, description="dataset (container) name")
    object_type: str = Field(
        min_length=1, max_length=200, description="contract table section name"
    )
    filter: list[ObjectFilter] = Field(default_factory=list, max_length=20)
    columns: list[str] | None = Field(default=None, max_length=100)
    id_column: str | None = Field(
        default=None,
        max_length=200,
        description="source-id column for tables without a contract identifier",
    )
    limit: int = Field(default=50, ge=1, le=500)
    offset: int = Field(default=0, ge=0)
    include_kg: bool = False
    include_rules: bool = True


@router.get("/types")
async def object_types(
    request: Request,
    dataset: str = Query(description="dataset (container) name"),
    _user=Depends(require_role(Role.VIEWER)),
    checker=Depends(get_checker),
) -> dict:
    """Contract table sections as object types (S8: no contract → empty).

    F1(review): contract STRUCTURE (table/identifier/lifecycle) is metadata
    the deny-guarded dataset endpoints protect — same dataset read check as
    /objects/query applies here.
    """
    # F1/F3: dataset-level read guard incl. scoped-token permissions
    if not checker.check_dataset_access(
        role=_user.role,
        dataset=dataset,
        action="read",
        permissions=getattr(_user, "permissions", None),
    ):
        raise HTTPException(
            status_code=403,
            detail=f"Read access to dataset '{dataset}' denied",
        )
    contract_store = getattr(request.app.state, "contract_store", None)
    if contract_store is None:
        raise HTTPException(status_code=503, detail="system_db disabled; contracts unavailable")
    latest = contract_store.get_version(dataset)
    if latest is None:
        return {"dataset": dataset, "has_contract": False, "types": []}
    from arrow_lake.contract.schema import parse_contract

    try:
        contract = parse_contract(latest["contract_yaml"])
    except Exception as exc:  # corrupt contract — surface, don't guess
        raise HTTPException(status_code=422, detail=f"Invalid contract: {exc}") from exc
    types = [
        {
            "table": tname,
            "object_class": sec.object_class,
            "lifecycle": None
            if sec.lifecycle is None
            else {
                "column": sec.lifecycle.column,
                "states": list(sec.lifecycle.states),
                "initial": sec.lifecycle.initial,
            },
            "identifier_column": (None if sec.identifier is None else sec.identifier.column),
        }
        for tname, sec in contract.tables.items()
    ]
    return {"dataset": dataset, "has_contract": True, "types": types}


@router.post("/query")
async def object_set_query(
    req: ObjectSetQueryRequest,
    request: Request,
    lake=Depends(get_lake),
    _user=Depends(require_role(Role.VIEWER)),
    checker=Depends(get_checker),
) -> dict:
    """Query business objects: composed SELECT → OLAP security path →
    per-row aggregation (identifier / links / kg / rules).

    W3.1(v1.11.2):取数管线(读权守卫→…→表过滤)提取至
    ``semantic/objectset.fetch_object_rows``,与 decisions/assess 共用;
    deny/ACL 闭包绑定本路由的 ``_deny_table_read``/``_acl_enforced_sql``
    ——安全路径字面同源,不建旁路。
    """
    from arrow_lake.api.routers.query import _acl_enforced_sql, _deny_table_read
    from arrow_lake.semantic.identity import parse_table_identifier
    from arrow_lake.semantic.objectset import fetch_object_rows

    contract_store = getattr(request.app.state, "contract_store", None)
    alignment_store = getattr(request.app.state, "semantic_alignment_store", None)
    entity_store = getattr(request.app.state, "entity_map_store", None)
    rules_store = getattr(request.app.state, "ontology_rules_store", None)

    res = await fetch_object_rows(
        lake=lake,
        checker=checker,
        role=_user.role,
        permissions=getattr(_user, "permissions", None),
        dataset=req.dataset,
        object_type=req.object_type,
        filters=[f.model_dump() for f in req.filter],
        columns=req.columns,
        id_column=req.id_column,
        limit=req.limit,
        offset=req.offset,
        contract_store=contract_store,
        alignment_store=alignment_store,
        deny_table_read=lambda n, t: _deny_table_read(n, t, request),
        acl_enforce=lambda sql, tgt: _acl_enforced_sql(sql, tgt, checker, _user.role),
    )

    # -- W4.3 聚合:每行 → 业务对象 ------------------------------------
    contract = res.contract
    section = contract.tables[req.object_type]
    ident_col = res.ident_col
    lc_col = res.lifecycle_col
    refs = [r for r in contract.references if r.from_table == req.object_type]
    declared = {r.name: r for r in section.columns}
    result_cols = list(res.result_columns)
    rows = res.rows

    col_meta: list[dict[str, Any]] = []
    for c in result_cols:
        m: dict[str, Any] = {"name": c}
        rule = declared.get(c)
        if rule is not None:
            if rule.label:
                m["label"] = rule.label
            if rule.unit:
                m["unit"] = rule.unit
        col_meta.append(m)

    kg_nodes: list[dict] = []
    kg_by_name: dict[str, dict] = {}
    kg_by_id: dict[str, dict] = {}
    kg_edges: list[dict] = []
    if req.include_kg:
        try:
            g = await lake.kg_get_graph(req.dataset, limit=_KG_GRAPH_FETCH_LIMIT)
            kg_nodes = g.get("nodes") or []
            kg_edges = g.get("edges") or []
            kg_by_name = {_norm(n.get("name", "")): n for n in kg_nodes if n.get("name")}
            kg_by_id = {str(n.get("id")): n for n in kg_nodes}
        except Exception:  # KG 是近似桥接面:任何失败降级为 miss
            logger.warning("objects_kg_enrichment_failed", exc_info=True)
    # F4(review): adjacency prebuilt once — the per-row edge scan was
    # O(rows × edges) in pure Python on the event loop.
    kg_adj: dict[str, list[tuple[dict, Any]]] = {}
    for e in kg_edges:
        s, t = str(e.get("source")), str(e.get("target"))
        rel = e.get("relation_type") or e.get("label")
        other = kg_by_id.get(t)
        if other is not None:
            kg_adj.setdefault(s, []).append((other, rel))
        other = kg_by_id.get(s)
        if other is not None:
            kg_adj.setdefault(t, []).append((other, rel))

    rules_payload: list[dict[str, Any]] = []
    if req.include_rules and rules_store is not None:
        # F10(review): condition_expr/source_ref are ADMIN-only fields — the
        # rules registry endpoint is ADMIN-gated; object viewers get the
        # conclusion-level view (rule_id/type/version/conclusion).
        is_admin = getattr(_user, "role", None) == Role.ADMIN
        for r in rules_store.list_rules(
            scope=req.dataset, status="active"
        ) + rules_store.list_rules(scope="*", status="active"):
            entry: dict[str, Any] = {
                "rule_id": r["rule_id"],
                "rule_type": r.get("rule_type"),
                "version": r.get("version"),
                "conclusion": r["conclusion"],
                "scope": r["scope"],
            }
            if is_admin:
                entry["condition_expr"] = r["condition_expr"]
                entry["source_ref"] = r["source_ref"]
            rules_payload.append(entry)

    objects: list[dict[str, Any]] = []
    # F2(review): batch the reverse entity lookups — one libSQL round-trip
    # off the event loop (was per-row sync N+1, up to 500 remote calls).
    id_map: dict[str, list[str]] = {}
    if entity_store is not None:
        need = set()
        for row in rows:
            raw = row.get(ident_col) if ident_col else None
            if raw is None:
                continue
            raw = str(raw)
            parse = (
                parse_table_identifier(contract, req.object_type, raw)
                if section.identifier is not None
                else None
            )
            if parse is not None and parse.matched:
                continue
            need.add(raw)
        if need:

            def _batch_lookup() -> dict[str, list[str]]:
                return entity_store.lookup_object_ids_batch(
                    scope=req.dataset,
                    table_name=req.object_type,
                    source_ids=sorted(need),
                )

            try:
                id_map = await run_sync(
                    _batch_lookup, timeout=_OBJ_TIMEOUT, label="objects_entity_lookup"
                )
            except Exception:
                logger.warning("objects_entity_lookup_failed", exc_info=True)
                id_map = {}

    for row in rows:
        raw_id = row.get(ident_col) if ident_col else None
        ident: dict[str, Any] = {"matched": False, "components": {}, "mapped": False}
        object_id: str | None = None
        if raw_id is not None:
            raw_id = str(raw_id)
            parse = (
                parse_table_identifier(contract, req.object_type, raw_id)
                if section.identifier is not None
                else None
            )
            if parse is not None and parse.matched:
                object_id = parse.object_id
                ident = {"matched": True, "components": parse.components, "mapped": False}
            else:
                cands = id_map.get(raw_id, [])
                if len(cands) == 1:
                    object_id = cands[0]
                    ident = {"matched": False, "components": {}, "mapped": True}
                else:
                    object_id = raw_id  # 保源值;歧义/无映射交上层判读

        obj: dict[str, Any] = {
            "object_id": object_id,
            "identifier": ident,
            "attributes": {c: row[c] for c in result_cols},
        }
        if lc_col and lc_col in row:
            obj["lifecycle_state"] = row[lc_col]
        obj["_links"] = [
            {
                "from_column": r.from_column,
                "value": row.get(r.from_column) if r.from_column in row else None,
                "to_table": r.to_table,
                "to_column": r.to_column,
                "to_dataset": r.to_dataset,
                "cardinality": r.cardinality,
                "kind": r.kind,
            }
            for r in refs
        ]
        if req.include_kg:
            node = kg_by_name.get(_norm(object_id)) if object_id else None
            if node is not None:
                nid = str(node.get("id"))
                neighbors = [
                    {"name": o.get("name"), "type": o.get("type"), "relation_type": rel}
                    for o, rel in kg_adj.get(nid, [])[:_KG_NEIGHBOR_CAP]
                ]
                obj["_kg"] = {
                    "matched": True,
                    "vertex": {
                        "name": node.get("name"),
                        "type": node.get("type"),
                        "label": node.get("label"),
                    },
                    "neighbors": neighbors,
                }
            else:
                obj["_kg"] = {"matched": False, "vertex": None, "neighbors": []}
        objects.append(obj)

    return {
        "dataset": req.dataset,
        "object_type": req.object_type,
        "count": len(objects),
        "columns": col_meta,
        "aligned": res.aligned,
        # F8(review): documented semantics — filters compare the RAW (pre-
        # alignment) column values, same caliber as admin row_filters; the
        # displayed attributes are aligned. Echo the PRE-enforcement SQL
        # (F10): the enforced form carries admin row-filter predicates.
        "filter_semantics": "raw",
        "objects": objects,
        "_rules": rules_payload,
        "sql": res.sql,
    }
