"""研判 API(v1.11.2 MS3 W3.3,F3.1)——POST /api/v1/decisions/assess。

VIEWER(S9:纯读+规则求值,行动才是 EDITOR)。对象读取经 W3.1 共享管线
——dataset 读权/表级 deny/行过滤/列 ACL 与 /objects/query 完全同路,
不建旁路;无契约 422(S8 同语义)。
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field

from arrow_lake.api.auth_models import Role
from arrow_lake.api.deps import get_checker, get_lake, require_role

router = APIRouter(prefix="/api/v1/decisions", tags=["decisions"])


class AssessRequest(BaseModel):
    dataset: str = Field(min_length=1, max_length=200, description="dataset (container) name")
    object_type: str = Field(
        min_length=1, max_length=200, description="contract table section name"
    )
    object_id: str = Field(
        min_length=1, max_length=500, description="identifier-column value of the object"
    )


@router.post("/assess", dependencies=[Depends(require_role(Role.VIEWER))])
async def assess(
    req: AssessRequest,
    request: Request,
    record_history: bool = Query(
        default=False,
        description="true=研判落 decisions_history(RLHF/飞轮数据面,EDITOR 写)",
    ),
    lake=Depends(get_lake),
    user=Depends(require_role(Role.VIEWER)),
    checker=Depends(get_checker),
) -> dict:
    """Assess one object:对齐后取数 → active 规则求值 → 结论+可行动作。"""
    from arrow_lake.api.routers.query import _acl_enforced_sql, _deny_table_read
    from arrow_lake.decisions.assess import assess_object

    contract_store = getattr(request.app.state, "contract_store", None)
    alignment_store = getattr(request.app.state, "semantic_alignment_store", None)
    rules_store = getattr(request.app.state, "ontology_rules_store", None)
    action_store = getattr(request.app.state, "action_store", None)
    if rules_store is None:
        raise HTTPException(
            status_code=503,
            detail="system_db disabled; rules registry unavailable",
        )

    result = await assess_object(
        lake=lake,
        checker=checker,
        role=user.role,
        permissions=getattr(user, "permissions", None),
        actor_sub=getattr(user, "sub", ""),
        dataset=req.dataset,
        object_type=req.object_type,
        object_id=req.object_id,
        contract_store=contract_store,
        alignment_store=alignment_store,
        rules_store=rules_store,
        action_store=action_store,
        deny_table_read=lambda n, t: _deny_table_read(n, t, request),
        acl_enforce=lambda sql, tgt: _acl_enforced_sql(sql, tgt, checker, user.role),
    )

    # opt-in 研判历史(RLHF/飞轮数据面,发版前清偿 D 项);写操作要求
    # EDITOR——VIEWER 只读即时求值语义不变
    if record_history:
        from arrow_lake.api.auth_models import Role as _Role

        if user.role != _Role.ADMIN and user.role != _Role.EDITOR:
            # 注意:此处勿再 from fastapi import HTTPException——函数内 import
            # 会把 HTTPException 绑成局部名,使本函数 50 行的提前 raise 抛
            # UnboundLocalError(503 变 500;测试 test_rules_store_missing_503 钉住)
            raise HTTPException(
                status_code=403,
                detail="recording decision history requires EDITOR",
            )
        hstore = getattr(request.app.state, "decisions_history_store", None)
        if hstore is not None:
            import contextlib

            with contextlib.suppress(Exception):  # 历史落库不阻塞研判
                hstore.record(
                    dataset=req.dataset, object_type=req.object_type,
                    object_id=req.object_id,
                    lifecycle_state=result.get("lifecycle_state"),
                    matched_rules=result.get("matched_rules") or 0,
                    rule_ids=result.get("rule_ids") or [],
                    conclusions=result.get("conclusions") or [],
                    confidence=result.get("confidence") or 1.0,
                    actor=getattr(user, "sub", ""),
                )
                result["history_recorded"] = True
    return result


@router.get(
    "/history/{dataset}",
    dependencies=[Depends(require_role(Role.ADMIN))],
)
async def decision_history(
    dataset: str,
    request: Request,
    threshold: float = Query(default=0.6, gt=0, lt=1,
                             description="低置信阈值(飞轮口径)"),
) -> dict:
    """研判历史只读摘要(hq-guide 第⑥步飞轮状态,2026-08-31)。

    ``total`` 用独立计数查询(list_history 的 limit 不代表总量);
    ``low_confidence`` 与飞轮 auto 同源同参。
    """
    hstore = getattr(request.app.state, "decisions_history_store", None)
    if hstore is None:
        raise HTTPException(
            status_code=503, detail="system_db disabled; history unavailable")
    recent = hstore.list_history(dataset, limit=5)
    low = hstore.low_confidence(dataset, threshold=threshold, limit=100)
    cur = hstore._db.execute(  # noqa: SLF001 — store 未封装 count,薄查询
        "SELECT COUNT(*) FROM decisions_history WHERE dataset = ?", (dataset,))
    total = int((cur.fetchone() if cur is not None else [0])[0])
    return {
        "dataset": dataset, "total": total,
        "low_confidence": len(low), "threshold": threshold,
        "recent": [{
            "object_id": r.get("object_id"),
            "object_type": r.get("object_type"),
            "confidence": r.get("confidence"),
            "matched_rules": r.get("matched_rules"),
            "assessed_at": r.get("assessed_at"),
        } for r in recent],
    }
