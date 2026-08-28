"""研判 API(v1.11.2 MS3 W3.3,F3.1)——POST /api/v1/decisions/assess。

VIEWER(S9:纯读+规则求值,行动才是 EDITOR)。对象读取经 W3.1 共享管线
——dataset 读权/表级 deny/行过滤/列 ACL 与 /objects/query 完全同路,
不建旁路;无契约 422(S8 同语义)。
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request
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
    lake=Depends(get_lake),
    _user=Depends(require_role(Role.VIEWER)),
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

    return await assess_object(
        lake=lake,
        checker=checker,
        role=_user.role,
        permissions=getattr(_user, "permissions", None),
        actor_sub=getattr(_user, "sub", ""),
        dataset=req.dataset,
        object_type=req.object_type,
        object_id=req.object_id,
        contract_store=contract_store,
        alignment_store=alignment_store,
        rules_store=rules_store,
        action_store=action_store,
        deny_table_read=lambda n, t: _deny_table_read(n, t, request),
        acl_enforce=lambda sql, tgt: _acl_enforced_sql(sql, tgt, checker, _user.role),
    )
