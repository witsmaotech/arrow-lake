"""标注项目注册 CRUD(v1.11.3 MS4 W1.4,F4.2)。

旁路面:全部 ADMIN;沿 actions 路由约定(system_db 关闭 → 503)。
项目 = AL 侧注册(名称/源数据集/绑定模板 + 生成的 LS label_config);
**不在创建时调 LS**——LS 是 transient 工作区,W2 dispatch 时才懒创建
LS project 并回填 ls_project_id(SoT 在本表,设计 §0 红线)。

labeling_config 两条路(S2):默认从绑定模板经 template_gen 生成;
``labeling_config_override`` 良构即透传(手写高级覆盖)。
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from arrow_lake.annotation.template_gen import TemplateGenError, generate_ls_config
from arrow_lake.api.auth_models import Role
from arrow_lake.api.deps import require_role

router = APIRouter(prefix="/api/v1/annotation", tags=["annotation"])


def _store(request: Request) -> Any:
    store = getattr(request.app.state, "annotation_project_store", None)
    if store is None:
        raise HTTPException(status_code=503, detail="system_db disabled; annotation registry unavailable")
    return store


def _template_yaml(name: str) -> str | None:
    """模板 gallery 按名取 YAML 文本(找不到 → None → 422)。"""
    from arrow_lake.knowledge_graph.doc_type_router import get_template_gallery

    for t in get_template_gallery().templates:
        if t.name == name.lower():
            with open(t.path, encoding="utf-8") as fh:
                return fh.read()
    return None


class ProjectCreate(BaseModel):
    name: str = Field(min_length=1, max_length=128, pattern=r"^[a-zA-Z0-9][a-zA-Z0-9._-]*$")
    dataset: str = Field(min_length=1, max_length=200)
    template_name: str = Field(min_length=1, max_length=128)
    labeling_config_override: str | None = Field(
        default=None, max_length=64_000,
        description="手写 LS XML 高级覆盖(S2);缺省从绑定模板生成",
    )


@router.get("/projects", dependencies=[Depends(require_role(Role.ADMIN))])
async def list_projects(request: Request) -> dict:
    projects = _store(request).list_projects()
    return {"total": len(projects), "projects": projects}


@router.get("/projects/{name}", dependencies=[Depends(require_role(Role.ADMIN))])
async def get_project(name: str, request: Request) -> dict:
    rec = _store(request).get_project(name)
    if rec is None:
        raise HTTPException(status_code=404, detail=f"No annotation project '{name}'")
    return rec


@router.post(
    "/projects",
    status_code=200,
    dependencies=[Depends(require_role(Role.ADMIN))],
)
async def create_project(req: ProjectCreate, request: Request) -> dict:
    store = _store(request)
    manual: str | None = None
    template_yaml: str = ""
    if req.labeling_config_override is not None:
        manual, config_source = req.labeling_config_override, "manual"
    else:
        template_yaml = _template_yaml(req.template_name) or ""
        if not template_yaml:
            raise HTTPException(
                status_code=422,
                detail=f"Unknown template '{req.template_name}' (not in gallery)",
            )
        config_source = "generated"
    try:
        cfg = generate_ls_config(template_yaml, manual_config=manual)
    except TemplateGenError as exc:
        raise HTTPException(status_code=422, detail=f"Invalid labeling config: {exc}") from exc
    rec = store.create_project(
        name=req.name,
        dataset=req.dataset,
        template_name=req.template_name,
        labeling_config=cfg.xml,
        config_source=config_source,
    )
    if rec is None:
        raise HTTPException(status_code=422, detail=f"Annotation project '{req.name}' already exists")
    return rec


@router.delete("/projects/{name}", dependencies=[Depends(require_role(Role.ADMIN))])
async def delete_project(name: str, request: Request) -> dict:
    if not _store(request).delete_project(name):
        raise HTTPException(status_code=404, detail=f"No annotation project '{name}'")
    return {"name": name, "deleted": True}
