"""v1.10.0 P6: admin endpoints for user extraction-template management.

CRUD over the writable user-templates volume (``/data/lake/templates/``).
Mutations validate the YAML (:mod:`template_registry`), write to disk, and
:func:`reset_gallery_cache` so the next ``kg_build`` picks up the change with no
rebuild/restart. System/project templates are read-only.

The libSQL metadata store (P3) is wired in M2 for the per-dataset binding UI;
M1 ships CRUD with the YAML as single source of truth (gallery lists everything).
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
import secrets
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from arrow_lake.api.auth_models import Role
from arrow_lake.api.deps import require_role, get_lake
from arrow_lake.api.utils import run_sync
from arrow_lake.knowledge_graph.doc_type_router import (
    get_template_gallery, reset_gallery_cache,
)
from arrow_lake.rag.provider import create_llm_provider, LLMMessage
from arrow_lake.knowledge_graph.template_registry import (
    TemplateValidationError,
    content_hash, delete_template, save_template, validate_template_yaml,
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/v1/admin/extraction-templates", tags=["admin"])

_USER_DIR_ENV = "ARROW_LAKE__HUGEGRAPH__HE_USER_TEMPLATES_DIR"
_USER_DIR_DEFAULT = "/data/lake/templates"


# --- helpers ---------------------------------------------------------------

def _user_dir() -> str:
    import os
    return os.environ.get(_USER_DIR_ENV, _USER_DIR_DEFAULT)


def _store(request: Request):
    """The extraction-template store (None when system_db is disabled — degrade)."""
    return getattr(request.app.state, "extraction_template_store", None)


def _reserved_names() -> set[str]:
    """System + project template names (user templates must not shadow them)."""
    return {t.name for t in get_template_gallery().templates if t.source == "system"}


def _find(name: str) -> Any | None:
    for t in get_template_gallery().templates:
        if t.name == name.lower():
            return t
    return None


def _inject_doc_type_tag(yaml_text: str, doc_type: str | None) -> str:
    """Add ``doc_type`` to the YAML's ``tags`` so gallery routing (Layer 2 tag
    match) finds it — fixes the v1.10.0 小坑 where the API ``doc_type`` field
    only reached system_db, not the routing gallery. Surgical regex (preserves
    user formatting); ``tags`` is a valid hyper-extract field (safe to touch).
    """
    import re
    if not doc_type:
        return yaml_text
    dt = doc_type.strip().lower()
    if not dt:
        return yaml_text
    # flow list:  tags: [a, b]
    m = re.search(r'^(\s*tags:\s*\[)([^\]]*)\]\s*$', yaml_text, re.M)
    if m:
        items = [x.strip().strip('"\'') for x in m.group(2).split(',') if x.strip()]
        if dt not in items:
            items.append(dt)
        return yaml_text[:m.start()] + f"{m.group(1)}{', '.join(items)}]" + yaml_text[m.end():]
    # block list:  tags:\n  - a\n  - b
    m = re.search(r'^(\s*tags:\s*\n)((?:[ \t]+-.*\n?)+)', yaml_text, re.M)
    if m:
        lines = [l for l in m.group(2).splitlines() if l.strip()]
        items = [re.search(r'-\s*(.+)', l).group(1).strip().strip('"\'') for l in lines]
        if dt not in items:
            indent = (re.match(r'([ \t]*)', lines[0]).group(1) if lines else "  ")
            block = m.group(2).rstrip("\n") + f"\n{indent}- {dt}\n"
        else:
            block = m.group(2)
        return yaml_text[:m.start()] + m.group(1) + block + yaml_text[m.end():]
    # no tags block → add `tags: [dt]` right after the `name:` line
    m = re.search(r'^(\s*name:\s*.*)$', yaml_text, re.M)
    if m:
        return yaml_text[:m.end()] + f"\ntags: [{dt}]" + yaml_text[m.end():]
    return yaml_text + f"\ntags: [{dt}]\n"


# --- models ----------------------------------------------------------------

class TemplateCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=64, pattern=r"^[a-z][a-z0-9_]*$")
    yaml: str = Field(..., min_length=1)
    doc_type: str | None = None
    description: str | None = None


class TemplateUpdate(BaseModel):
    yaml: str = Field(..., min_length=1)
    doc_type: str | None = None
    description: str | None = None


class TemplateValidateRequest(BaseModel):
    yaml: str = Field(..., min_length=1)


# --- endpoints -------------------------------------------------------------

@router.get("")
async def list_templates(
    source: str | None = None,
    selectable: bool = False,
    _user: dict = Depends(require_role(Role.ADMIN)),
) -> dict:
    """List extraction templates (system + project read-only, user editable)."""
    templates = get_template_gallery().templates
    if source:
        templates = [t for t in templates if t.source == source]
    items = []
    for t in templates:
        s = t.to_summary()
        if selectable:
            s["selectable"] = True  # all indexed templates are bindable
        items.append(s)
    return {"success": True, "data": items, "count": len(items)}


@router.get("/{name}")
async def get_template(
    name: str,
    _user: dict = Depends(require_role(Role.ADMIN)),
) -> dict:
    """Detail: parsed metadata + raw YAML text (user/project file OR preset)."""
    t = _find(name)
    if t is None:
        raise HTTPException(status_code=404, detail=f"template not found: {name}")
    detail = t.to_detail()
    detail["yaml"] = _read_yaml(t.path)
    return {"success": True, "data": detail}


def _read_yaml(path: str) -> str | None:
    """Read raw YAML for a template path (file path OR preset ``category/name``)."""
    # user/project template: absolute .yaml file path
    if path.endswith(".yaml"):
        try:
            with open(path, encoding="utf-8") as fh:
                return fh.read()
        except OSError:
            return None
    # preset: category/name → <hyperextract>/templates/presets/<category>/<name>.yaml
    if "/" in path:
        try:
            import hyperextract
            import os
            cat, _, nm = path.partition("/")
            preset_file = os.path.join(
                os.path.dirname(hyperextract.__file__), "templates", "presets", cat, f"{nm}.yaml")
            if os.path.isfile(preset_file):
                with open(preset_file, encoding="utf-8") as fh:
                    return fh.read()
        except Exception:  # noqa: BLE001 — hyperextract missing / unreadable preset
            return None
    return None


@router.post("/validate")
async def validate(
    req: TemplateValidateRequest,
    _user: dict = Depends(require_role(Role.ADMIN)),
) -> dict:
    """Validate a YAML without saving. Returns field-level errors."""
    try:
        validate_template_yaml(req.yaml, reserved_names=_reserved_names())
        return {"success": True, "data": {"valid": True}}
    except TemplateValidationError as exc:
        return {"success": True, "data": {
            "valid": False, "errors": [{"path": p, "message": m} for p, m in exc.errors]}}


# --- LLM-assisted generation (M2.5) ---------------------------------------

class GenerateRequest(BaseModel):
    prompt: str = Field(..., min_length=2, max_length=1000, description="领域描述")
    sample_text: str | None = Field(default=None, max_length=3000)
    doc_type: str | None = None
    base: str | None = "project_concept_graph"  # few-shot 蓝本


_generate_sem = asyncio.Semaphore(3)  # 限并发,防 LLM 代理滥用

_GEN_SYSTEM = """你是 hyper-extract 知识抽取模板设计专家。只输出一个合法模板的 YAML,严格符合下述 schema。

【输出 schema】(字段名、层级、缩进必须完全一致;下面是一个结构范例)
language: [zh, en]
name: security_concept_graph
type: graph
tags: [security, asset, threat]
description: {zh: 安全概念图, en: security concept graph}
output:
  entities:
    description: {zh: 文档中的安全要素, en: security elements}
    fields:
      - {name: name, type: str, description: {zh: 要素名称(规范术语), en: name}, required: true}
      - {name: type, type: str, description: {zh: "必须是以下之一: 资产/威胁/控制/事件", en: "one of: asset/threat/control/event"}, required: true}
      - {name: definition, type: str, description: {zh: 要素定义, en: definition}, required: true}
  relations:
    description: {zh: 安全要素间关联, en: relations}
    fields:
      - {name: source, type: str, description: {zh: 源要素, en: source}, required: true}
      - {name: target, type: str, description: {zh: 目标要素, en: target}, required: true}
      - {name: type, type: str, description: {zh: "关系类型,必须是: 防护/利用/触发/相关", en: relation type}, required: true}
guideline:
  target: {zh: 你是网络安全知识图谱专家, en: you are a security kg expert}
  rules_for_entities:
    zh: [每段最多提取 12 个核心要素, type 必须按枚举, name 用规范术语]
    en: [extract up to 12 core elements per chunk]

【硬约束 — 任一不满足会被校验拒绝】
1. 只输出纯 YAML。首行必须是 language: [zh, en]。禁止 markdown 代码围栏,禁止任何解释/前后缀/对话文字。
2. output.entities.fields 必须包含 name 字段;output.relations.fields 必须包含 source、target、type 三个字段。
3. 每个字段必须有 name/type/description;字段 type 只能是 str/int/float/bool。
4. 顶层 type 只能是 graph(除非用户明确要求 model 或 hypergraph)。
5. guideline.target 必须有 zh 和 en。
6. entity 建议 3-6 个字段(至少 name/type/definition);relations 至少 source/target/type。
7. 把该领域的核心实体类型作为枚举写进 entity.type 的 description(如"必须是 A/B/C 之一"),把关系类型枚举写进 relation.type 的 description —— 这是约束抽取质量的关键。
8. name 用领域派生的英文小写下划线标识符(不要用范例的 security_concept_graph,除非领域就是安全)。

【参考蓝本】(仅供学习结构与写法,严禁照抄其领域内容):
{BASE}
"""


def _strip_fences(text: str) -> str:
    """剥 markdown 代码围栏 + 前后空白/解释,只留 YAML 主体。"""
    import re
    t = text.strip()
    # 去代码围栏 ```yaml ... ``` 或 ``` ... ```
    m = re.search(r"```(?:ya?ml|yaml)?\s*\n(.*?)```", t, re.S)
    if m:
        t = m.group(1).strip()
    # 若开头不是 yaml 键,截到第一个 `language:` 或 `name:`
    if not t.startswith(("language", "name", "type", "tags")):
        idx = t.find("language:")
        if idx == -1:
            idx = t.find("name:")
        if idx > 0:
            t = t[idx:]
    return t.strip()


def _hyperextract_check(yaml_text: str) -> tuple[bool, str]:
    """Authoritative 校验:让 hyper-extract 真实加载模板(比 registry 严)。

    生成的模板过了 ``validate_template_yaml`` 仍可能被 hyper-extract 的
    TemplateCfg 拒(字段必填/枚举/结构)→ 实际抽取 0 实体。这里用
    ``Template.create(path)``(不传 llm_client,只解析)做真实加载校验。
    hyperextract 未装(如 host 测试环境)→ 跳过返 (True, "") 只靠 registry。
    """
    try:
        from hyperextract import Template
    except ImportError:
        return True, ""
    import os
    import tempfile
    fd, tmp = tempfile.mkstemp(suffix=".yaml")
    try:
        os.write(fd, yaml_text.encode("utf-8")); os.close(fd)
        Template.create(tmp)  # 不传 llm_client/embedder,仅解析模板结构
        return True, ""
    except Exception as exc:  # TemplateCfg ValidationError 等
        return False, f"hyper-extract 加载拒绝: {str(exc)[:240]}"
    finally:
        try:
            os.remove(tmp)
        except OSError:
            pass


async def _do_generate(req: "GenerateRequest", generate_fn) -> tuple[str, list, bool]:
    """生成 + self-heal。generate_fn: async ([(role, content), ...]) -> str。
    返回 (yaml_text, errors, healed)。errors 空 = 校验通过。"""
    base_yaml = ""
    if req.base:
        t = _find(req.base)
        if t is not None:
            by = _read_yaml(t.path) or ""
            base_yaml = by[:1800]  # 节选,控 prompt 长度
    system = _GEN_SYSTEM.replace("{BASE}", base_yaml)
    user = f"领域描述:{req.prompt.strip()}\n"
    if req.doc_type:
        user += f"doc_type: {req.doc_type.strip()}\n"
    if req.sample_text:
        user += f"\n样本文档(节选,据此推断该抽哪些实体/关系):\n{req.sample_text.strip()[:1500]}\n"
    user += "\n请只输出符合 schema 的纯 YAML。"
    msgs: list[tuple[str, str]] = [("system", system), ("user", user)]
    last_yaml, last_errors = "", []
    for attempt in range(3):  # 初次 + 最多 2 轮 self-heal
        out = await generate_fn(msgs)
        yaml_text = _strip_fences(out)
        last_yaml = yaml_text
        # 1. registry 结构校验(快)
        try:
            validate_template_yaml(yaml_text)
        except TemplateValidationError as exc:
            last_errors = [(p, m) for p, m in exc.errors]
            msgs.append(("assistant", out))
            msgs.append(("user", "校验失败 " + str(len(last_errors)) + " 处: "
                         + "; ".join(f"{p}: {m}" for p, m in last_errors[:6])
                         + "。请只输出修正后的完整纯 YAML。"))
            continue
        # 2. hyper-extract 真实加载校验(authoritative;修 E2E gap:registry 过但 HE 拒→0实体)
        ok, he_err = _hyperextract_check(yaml_text)
        if not ok:
            last_errors = [("hyper-extract", he_err)]
            msgs.append(("assistant", out))
            msgs.append(("user", he_err + " 常见原因:字段缺失/必填项/output.entities 或 relations 缺 description/guideline 结构不全/字段 type 不合法。请只输出修正后的完整纯 YAML。"))
            continue
        return yaml_text, [], attempt > 0
    return last_yaml, last_errors, True


@router.post("/generate")
async def generate_template(
    req: GenerateRequest,
    lake: Any = Depends(get_lake),
    _user: dict = Depends(require_role(Role.ADMIN)),
) -> dict:
    """LLM 生成模板 YAML(self-heal,不落盘)。返 {yaml, errors, healed}。"""
    async with _generate_sem:
        # 抽取阶段 LLM(he_extract_llm 优先,回退全局 llm)
        hg = getattr(lake._config, "hugegraph", None)
        cfg = getattr(hg, "he_extract_llm", None) or getattr(lake._config, "llm", None)
        if cfg is None or not getattr(cfg, "api_key", None):
            raise HTTPException(status_code=503, detail="未配置 LLM(he_extract_llm/llm),无法生成")

        async def generate_fn(msgs: list[tuple[str, str]]) -> str:
            provider = create_llm_provider(cfg)
            resp = await provider.generate([LLMMessage(role=r, content=c) for r, c in msgs])
            return getattr(resp, "content", "") or ""

        try:
            yaml_text, errors, healed = await asyncio.wait_for(_do_generate(req, generate_fn), timeout=60)
        except asyncio.TimeoutError:
            raise HTTPException(status_code=504, detail="LLM 生成超时(>60s)")
        except Exception as exc:  # noqa: BLE001 — LLM/provider 故障
            raise HTTPException(status_code=502, detail=f"LLM 生成失败: {str(exc)[:160]}")
    logger.info("template_generated by=%s healed=%s valid=%s",
                getattr(_user, "username", None), healed, not errors)
    return {"success": True, "data": {"yaml": yaml_text, "errors": [
        {"path": p, "message": m} for p, m in errors], "healed": healed,
        "valid": not errors}}


# --- dry-run 试跑(M3)-----------------------------------------------------

class DryRunRequest(BaseModel):
    yaml: str | None = Field(default=None, description="模板 YAML(与 template_name 二选一)")
    template_name: str | None = None
    sample_text: str = Field(..., min_length=2, max_length=8000)


_dryrun_sem = asyncio.Semaphore(3)
_DRYRUN_DIR = "/data/lake/.template-dryrun"  # 卷上临时目录(user 模板 glob 不递归,不会污染 gallery)


def _result_sample(result, max_e: int = 30, max_r: int = 20) -> tuple[list, list]:
    def props(p):
        return {str(k): ("" if v is None else str(v)) for k, v in (p or [])}
    ents = [{"name": e.name, "type": e.entity_type, "properties": props(e.properties)}
            for e in (getattr(result, "entities", None) or [])[:max_e]]
    rels = [{"source": r.source, "target": r.target, "type": r.relation_type,
             "properties": props(r.properties)}
            for r in (getattr(result, "relations", None) or [])[:max_r]]
    return ents, rels


@router.post("/dry-run")
async def dry_run(
    req: DryRunRequest,
    lake: Any = Depends(get_lake),
    _user: dict = Depends(require_role(Role.ADMIN)),
) -> dict:
    """试跑:用模板对样本文本抽一次,返实体/关系样本。不落盘、不建图。"""
    yaml_text = req.yaml
    if not yaml_text and req.template_name:
        t = _find(req.template_name)
        if t is None:
            raise HTTPException(status_code=404, detail=f"template not found: {req.template_name}")
        yaml_text = _read_yaml(t.path)
    if not yaml_text:
        raise HTTPException(status_code=422, detail="需提供 yaml 或 template_name")
    try:
        validate_template_yaml(yaml_text)
    except TemplateValidationError as exc:
        raise HTTPException(status_code=422, detail={
            "code": "TEMPLATE_INVALID",
            "errors": [{"path": p, "message": m} for p, m in exc.errors]}) from exc

    import os
    import secrets
    import time
    async with _dryrun_sem:
        os.makedirs(_DRYRUN_DIR, exist_ok=True)
        token = secrets.token_hex(8)
        tmp = os.path.join(_DRYRUN_DIR, f"{token}.yaml")
        try:
            with open(tmp, "w", encoding="utf-8") as fh:
                fh.write(yaml_text)
            ext = lake._get_kg_extractor()
            if ext is None:
                raise HTTPException(status_code=503, detail="KG 抽取器不可用(检查 hugegraph.enabled / LLM 配置)")
            ext._active_template_override = tmp  # 复用 _resolve_template 单 chokepoint
            t0 = time.perf_counter()
            try:
                result = await asyncio.wait_for(
                    ext.extract(req.sample_text.strip(), chunk_id="dryrun"), timeout=60)
            finally:
                ext._active_template_override = None
            ents, rels = _result_sample(result)
            elapsed = int((time.perf_counter() - t0) * 1000)
            logger.info("template_dry_run by=%s entities=%d relations=%d elapsed=%dms",
                        getattr(_user, "username", None), len(ents), len(rels), elapsed)
            return {"success": True, "data": {
                "entities": ents, "relations": rels,
                "entity_count": len(getattr(result, "entities", None) or []),
                "relation_count": len(getattr(result, "relations", None) or []),
                "elapsed_ms": elapsed}}
        except asyncio.TimeoutError:
            raise HTTPException(status_code=504, detail="试跑超时(>60s)")
        finally:
            try:
                os.remove(tmp)
            except OSError:
                pass


# --- quality validation harness(M4,方案 B 真 HugeGraph)-------------------
# 模板质量端到端验证:LLM 按模板生成 ~2000字 场景文档 → ingest+index → kg_build
# (template=name) → 前端 vis-network 可视化 + RAG 问答 → 清理临时数据集。
# 比 M3 dry-run(单 chunk 抽样)更彻底:多 chunk 合并 + 真 HugeGraph + RAG。

class QualityDocRequest(BaseModel):
    scenario_hint: str | None = Field(default=None, max_length=500)


class QualityBuildRequest(BaseModel):
    document: str = Field(..., min_length=50, max_length=20000)


_quality_sem = asyncio.Semaphore(2)  # ingest+build 重,限并发
_QUALITY_DIR = "/data/lake/template-quality-md"  # 卷上临时 .md 目录,不进 user 模板 glob
_QUALITY_KA_DIR = "/data/lake/template-quality-ka"  # 隔离的 KA dump/checkpoint 根(与 /data/lake/ka 同级,生产隔离)
# 临时数据集严格匹配 build 生成的 _quality_<token_hex(6)>(12 个小写 hex)。cleanup 路径
# 派生用它前先校验,杜绝 path-traversal(恶意 temp_ds 经 _quality_ka_root 派生 .. 可越界 rmtree)。
_QUALITY_DS_RE = re.compile(r"^_quality_[0-9a-f]{12}$")


def _quality_ka_root(temp_ds: str) -> str:
    """Sharded quality-KA base dir for a temp dataset.

    ``/data/lake/template-quality-ka/<tt>`` where ``<tt>`` is the 2-char token
    prefix — git-style sharding so a single flat dir doesn't grow unbounded as
    concurrent / un-cleaned runs accumulate. Derived purely from ``temp_ds``
    (``_quality_<token>``), so build dispatch and cleanup always agree.

    Caller MUST have validated ``temp_ds`` against ``_QUALITY_DS_RE`` first — the
    shard is sliced from the raw name, so an unsanitized value could traverse.
    """
    token = temp_ds[len("_quality_"):]
    return os.path.join(_QUALITY_KA_DIR, token[:2])


def _get_extract_llm_cfg(lake) -> Any:
    """抽取/生成用 LLM 配置(he_extract_llm 优先,回退全局 llm)。"""
    hg = getattr(lake._config, "hugegraph", None)
    return getattr(hg, "he_extract_llm", None) or getattr(lake._config, "llm", None)


def _extract_schema_snippet(yaml_text: str) -> str:
    """取模板的 output/guideline 块喂给 doc-gen LLM —— 这些字段的 description
    带着实体/关系类型枚举,LLM 据此覆盖所有类型。解析失败回退裁剪后的原始 YAML。"""
    try:
        import yaml as _yaml
        d = _yaml.safe_load(yaml_text) or {}
        keep = {k: d[k] for k in ("name", "type", "output", "guideline", "description") if k in d}
        return _yaml.safe_dump(keep, allow_unicode=True, sort_keys=False)
    except Exception:  # noqa: BLE001 — 解析失败不阻塞,喂原始文本
        return yaml_text[:4000]


def _strip_doc_fences(text: str) -> str:
    """剥可能的 markdown 代码围栏 + 前后空白。文档是自由文本,只去围栏不过滤内容。"""
    t = (text or "").strip()
    m = re.search(r"```(?:markdown|md|text)?\s*\n(.*?)```", t, re.S)
    return m.group(1).strip() if m else t


_QUALITY_DOC_SYSTEM = """你是领域场景文档撰写专家。下面给出一个 hyper-extract 知识抽取模板的定义。请撰写一篇真实、连贯、信息密集的中文场景文档(约 2000 字),用于端到端验证该模板的抽取能力。

【硬约束】
1. 先从模板 output.entities.fields 里 type 字段的说明读出**所有实体类型**(通常是枚举,如"必须是 资产/威胁/控制 之一")。文档中**每一种实体类型至少出现 2 个具体实例**,用规范术语命名,自然嵌入叙述。
2. 从 output.relations.fields 里 type 字段的说明读出**所有关系类型**。文档中**每一种关系类型至少被 1 对实体实例真实体现**(可被抽取的关系)。
3. 文档必须是**连贯的真实叙事**(一份完整的报告/案例/方案/纪要),有背景、过程、结论。**禁止**用清单、表格、JSON 或罗列实体来凑数。
4. 字数约 1500-2500 字(中文)。只输出正文:不要解释、不要元注释、不要 markdown 代码围栏。

【模板定义】:
{TEMPLATE}
"""


@router.post("/{name}/quality/doc")
async def quality_doc(
    name: str,
    req: QualityDocRequest,
    lake: Any = Depends(get_lake),
    _user: dict = Depends(require_role(Role.ADMIN)),
) -> dict:
    """质量验证·生成文档:LLM 读模板 → 生成 ~2000字 场景文档(强制覆盖所有实体/关系类型)。"""
    t = _find(name)
    if t is None:
        raise HTTPException(status_code=404, detail=f"template not found: {name}")
    yaml_text = _read_yaml(t.path) or ""
    if not yaml_text:
        raise HTTPException(status_code=422, detail="模板 YAML 不可读(无法生成文档)")
    cfg = _get_extract_llm_cfg(lake)
    if cfg is None or not getattr(cfg, "api_key", None):
        raise HTTPException(status_code=503, detail="未配置 LLM(he_extract_llm/llm),无法生成文档")

    system = _QUALITY_DOC_SYSTEM.replace("{TEMPLATE}", _extract_schema_snippet(yaml_text))
    user_msg = ""
    if req.scenario_hint and req.scenario_hint.strip():
        user_msg += f"场景提示:{req.scenario_hint.strip()}\n"
    user_msg += "请撰写约 2000 字的中文场景文档,严格覆盖模板定义的全部实体类型与关系类型,只输出正文。"
    msgs: list[tuple[str, str]] = [("system", system), ("user", user_msg)]

    async def generate_fn(m):
        provider = create_llm_provider(cfg)
        resp = await provider.generate([LLMMessage(role=r, content=c) for r, c in m])
        return getattr(resp, "content", "") or ""

    async with _generate_sem:  # 复用 generate 的并发闸
        try:
            out = await asyncio.wait_for(generate_fn(msgs), timeout=90)
        except asyncio.TimeoutError:
            raise HTTPException(status_code=504, detail="LLM 生成文档超时(>90s)")
        except Exception as exc:  # noqa: BLE001 — LLM/provider 故障
            raise HTTPException(status_code=502, detail=f"LLM 生成失败: {str(exc)[:160]}") from exc
    document = _strip_doc_fences(out)
    logger.info("template_quality_doc template=%s doc_len=%d by=%s",
                name, len(document), getattr(_user, "username", None))
    return {"success": True, "data": {"document": document}}


async def _safe_delete_quality(lake, temp_ds: str) -> None:
    """Best-effort 清理临时质量数据集 + 其隔离 KA 目录(delete_dataset 已会 drop kg 图)。"""
    # defense-in-depth:先严格校验 temp_ds 形如 _quality_<hex>。_quality_ka_root 从裸名
    # 切 shard,未校验则 .. 可越界 rmtree;delete_dataset 也会因非法名被拒。
    if not _QUALITY_DS_RE.match(temp_ds):
        return
    try:
        await run_sync(lake.delete_dataset, temp_ds, timeout=120, label="quality_cleanup")
    except Exception:  # noqa: BLE001 — 清理永不抛
        pass
    # delete_dataset 不删 KA dump/checkpoint → 手动删隔离 KA 目录。
    try:
        import shutil
        from arrow_lake.knowledge_graph._naming import artifact_key_for
        ka_key_dir = os.path.join(_quality_ka_root(temp_ds), artifact_key_for(temp_ds))
        await run_sync(shutil.rmtree, ka_key_dir, True, timeout=60, label="quality_ka_cleanup")
    except Exception:  # noqa: BLE001
        pass


@router.post("/{name}/quality/build")
async def quality_build(
    name: str,
    req: QualityBuildRequest,
    lake: Any = Depends(get_lake),
    _user: dict = Depends(require_role(Role.ADMIN)),
) -> dict:
    """质量验证·建图:写 .md → ingest+index → kg_build(template=name)。
    返回临时数据集名 + kg 任务 id(build 异步,前端轮询 /kg/build/{id}/status)。"""
    if _find(name) is None:
        raise HTTPException(status_code=404, detail=f"template not found: {name}")
    async with _quality_sem:
        os.makedirs(_QUALITY_DIR, exist_ok=True)
        token = secrets.token_hex(6)
        temp_ds = f"_quality_{token}"
        md_path = os.path.join(_QUALITY_DIR, f"{token}.md")
        with open(md_path, "w", encoding="utf-8") as fh:
            fh.write(req.document.strip())
        try:
            await run_sync(
                lake.ingest_documents_and_index, temp_ds, [md_path],
                timeout=600, label="quality_ingest",
                actor=getattr(_user, "username", None) or "system",
            )
        except Exception as exc:
            await _safe_delete_quality(lake, temp_ds)
            raise HTTPException(status_code=502, detail=f"ingest 失败: {str(exc)[:160]}") from exc
        finally:
            try:
                os.remove(md_path)  # 数据已入 lance/minio,删临时 .md
            except OSError:
                pass
        try:
            task_id = await lake.kg_build(temp_ds, template=name,
                                          ka_base_dir=_quality_ka_root(temp_ds))
        except Exception as exc:
            await _safe_delete_quality(lake, temp_ds)
            raise HTTPException(status_code=502, detail=f"kg_build 派发失败: {str(exc)[:160]}") from exc
        logger.info("template_quality_build template=%s temp_ds=%s task=%s by=%s",
                    name, temp_ds, task_id, getattr(_user, "username", None))
        return {"success": True, "data": {"temp_dataset": temp_ds, "kg_task_id": task_id}}


@router.delete("/quality/{temp_dataset}")
async def quality_cleanup(
    temp_dataset: str,
    lake: Any = Depends(get_lake),
    _user: dict = Depends(require_role(Role.ADMIN)),
) -> dict:
    """质量验证·清理:删临时数据集(同时 best-effort drop 其 kg_{temp} 图)。"""
    if not _QUALITY_DS_RE.match(temp_dataset):
        raise HTTPException(status_code=422, detail={
            "code": "INVALID_TEMP_DATASET",
            "message": "temp_dataset must be a quality-harness generated name (_quality_<12hex>)",
        })
    await _safe_delete_quality(lake, temp_dataset)
    logger.info("template_quality_cleanup temp_ds=%s by=%s",
                temp_dataset, getattr(_user, "username", None))
    return {"success": True, "data": {"temp_dataset": temp_dataset, "deleted": True}}


# --- quality run history(M4:持久化验证结果,可回看)------------------------

_GRAPH_SNAPSHOT_MAX_NODES = 500  # 超过则不存快照(只存计数),控 libSQL 行大小
_GRAPH_SNAPSHOT_MAX_EDGES = 2000


class QualitySaveRequest(BaseModel):
    document: str = Field(..., min_length=50, max_length=20000)
    scenario_hint: str | None = Field(default=None, max_length=500)
    temp_dataset: str | None = None
    entity_count: int = Field(default=0, ge=0)
    relation_count: int = Field(default=0, ge=0)
    graph_snapshot: dict | None = None  # {nodes, edges} from /kg/graph
    rag_qa: list[dict] | None = Field(default=None, max_length=50)  # [{question, answer, citations}]
    note: str | None = Field(default=None, max_length=1000)


def _quality_store(request: Request):
    return getattr(request.app.state, "template_quality_store", None)


@router.post("/{name}/quality/save")
async def quality_save(
    name: str,
    req: QualitySaveRequest,
    request: Request,
    _user: dict = Depends(require_role(Role.ADMIN)),
) -> dict:
    """质量验证·保存:持久化本次验证(文档/计数/图快照/RAG 问答),供历史回看。"""
    store = _quality_store(request)
    if store is None:
        raise HTTPException(status_code=503, detail="system_db disabled; history unavailable")
    import json
    snap = None
    if req.graph_snapshot and len(req.graph_snapshot.get("nodes") or []) <= _GRAPH_SNAPSHOT_MAX_NODES \
            and len(req.graph_snapshot.get("edges") or []) <= _GRAPH_SNAPSHOT_MAX_EDGES:
        snap = json.dumps(req.graph_snapshot, ensure_ascii=False)
    rag = json.dumps(req.rag_qa or [], ensure_ascii=False) if req.rag_qa else None
    run_id = store.save_run(
        template_name=name, document=req.document, scenario_hint=req.scenario_hint,
        temp_dataset=req.temp_dataset, entity_count=req.entity_count,
        relation_count=req.relation_count, graph_snapshot=snap, rag_qa=rag,
        note=req.note, created_by=getattr(_user, "username", None),
    )
    logger.info("template_quality_saved template=%s run=%s by=%s",
                name, run_id, getattr(_user, "username", None))
    return {"success": True, "data": {"run_id": run_id}}


@router.get("/{name}/quality/history")
async def quality_history(
    name: str,
    request: Request,
    _user: dict = Depends(require_role(Role.ADMIN)),
) -> dict:
    """质量验证·历史:列出某模板的历次验证(摘要,不含大字段)。"""
    store = _quality_store(request)
    if store is None:
        raise HTTPException(status_code=503, detail="system_db disabled; history unavailable")
    runs = store.list_runs(name)
    return {"success": True, "data": runs, "count": len(runs)}


@router.get("/quality/runs/{run_id}")
async def quality_run_detail(
    run_id: int,
    request: Request,
    _user: dict = Depends(require_role(Role.ADMIN)),
) -> dict:
    """质量验证·详情:单次验证全量(含文档/图快照/RAG 问答),供回看重放。"""
    store = _quality_store(request)
    if store is None:
        raise HTTPException(status_code=503, detail="system_db disabled; history unavailable")
    import json
    run = store.get_run(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail=f"run {run_id} not found")
    # 解包 JSON 字段为结构,前端直接用
    run["graph_snapshot"] = json.loads(run["graph_snapshot"]) if run.get("graph_snapshot") else None
    run["rag_qa"] = json.loads(run["rag_qa"]) if run.get("rag_qa") else []
    return {"success": True, "data": run}


@router.delete("/quality/runs/{run_id}")
async def quality_run_delete(
    run_id: int,
    request: Request,
    _user: dict = Depends(require_role(Role.ADMIN)),
) -> dict:
    """质量验证·删历史记录(仅删库记录,不影响任何数据集/图)。"""
    store = _quality_store(request)
    if store is None:
        raise HTTPException(status_code=503, detail="system_db disabled; history unavailable")
    removed = store.delete_run(run_id)
    if not removed:
        raise HTTPException(status_code=404, detail=f"run {run_id} not found")
    logger.info("template_quality_run_deleted run=%s by=%s",
                run_id, getattr(_user, "username", None))
    return {"success": True, "data": {"run_id": run_id, "deleted": True}}


# --- set-default + usage(M3)----------------------------------------------

class DefaultRequest(BaseModel):
    doc_type: str = Field(..., min_length=1)
    template: str = Field(..., min_length=1)


@router.put("/default")
async def set_default(
    req: DefaultRequest,
    request: Request,
    lake: Any = Depends(get_lake),
    _user: dict = Depends(require_role(Role.ADMIN)),
) -> dict:
    """设某 doc_type 的默认模板。返回 effective + shadowed_by_config(M2 review)。"""
    if _find(req.template) is None:
        raise HTTPException(status_code=404, detail=f"template not found: {req.template}")
    store = _store(request)
    if store is None:
        raise HTTPException(status_code=503, detail="system_db disabled; set-default unavailable")
    store.set_default(req.doc_type, req.template)
    # 运维 config override (he_doc_type_templates) 是否已覆盖该 doc_type?
    hg = getattr(lake._config, "hugegraph", None)
    overrides = getattr(hg, "he_doc_type_templates", None) or {}
    shadowed = req.doc_type in overrides
    logger.info("template_default_set doc_type=%s template=%s shadowed=%s by=%s",
                req.doc_type, req.template, shadowed, getattr(_user, "username", None))
    return {"success": True, "data": {"doc_type": req.doc_type, "template": req.template,
            "effective": not shadowed, "shadowed_by_config": shadowed}}


@router.get("/{name}/usage")
async def template_usage(
    name: str,
    request: Request,
    _user: dict = Depends(require_role(Role.ADMIN)),
) -> dict:
    """该模板被哪些数据集绑定(删前确认)。"""
    store = _store(request)
    bound = store.list_bindings(name) if store is not None else []
    return {"success": True, "data": {"template": name, "bound_datasets": bound}}


@router.post("", status_code=201)
async def create_template(
    req: TemplateCreate,
    _user: dict = Depends(require_role(Role.ADMIN)),
) -> dict:
    """Create a user template: validate → write → refresh gallery cache."""
    if req.name in _reserved_names():
        raise HTTPException(status_code=409, detail=f"template name conflicts with system template: {req.name}")
    try:
        yaml = _inject_doc_type_tag(req.yaml, req.doc_type)
        path = save_template(req.name, yaml, _user_dir(), reserved_names=_reserved_names())
    except TemplateValidationError as exc:
        raise HTTPException(status_code=422, detail={
            "code": "TEMPLATE_INVALID",
            "errors": [{"path": p, "message": m} for p, m in exc.errors]}) from exc
    reset_gallery_cache()
    logger.info("extraction_template_created name=%s by=%s hash=%s",
                req.name, getattr(_user, "username", None), content_hash(req.yaml)[:12])
    return {"success": True, "data": {"name": req.name, "path": str(path), "source": "user"}}


@router.put("/{name}")
async def update_template(
    name: str,
    req: TemplateUpdate,
    _user: dict = Depends(require_role(Role.ADMIN)),
) -> dict:
    """Update a user template (system/project → 403)."""
    existing = _find(name)
    if existing is not None and existing.source != "user":
        raise HTTPException(status_code=403, detail={"code": "TEMPLATE_READ_ONLY",
                                                      "message": f"{name} is a read-only {existing.source} template"})
    try:
        yaml = _inject_doc_type_tag(req.yaml, req.doc_type)
        path = save_template(name, yaml, _user_dir(), reserved_names=_reserved_names())
    except TemplateValidationError as exc:
        raise HTTPException(status_code=422, detail={
            "code": "TEMPLATE_INVALID",
            "errors": [{"path": p, "message": m} for p, m in exc.errors]}) from exc
    reset_gallery_cache()
    logger.info("extraction_template_updated name=%s by=%s hash=%s",
                name, getattr(_user, "username", None), content_hash(req.yaml)[:12])
    return {"success": True, "data": {"name": name, "path": str(path)}}


@router.delete("/{name}")
async def remove_template(
    name: str,
    request: Request,
    _user: dict = Depends(require_role(Role.ADMIN)),
) -> dict:
    """Delete a user template (system/project → 403; in-use → 422)."""
    existing = _find(name)
    if existing is not None and existing.source != "user":
        raise HTTPException(status_code=403, detail={"code": "TEMPLATE_READ_ONLY",
                                                      "message": f"{name} is a read-only {existing.source} template"})
    store = _store(request)
    if store is not None:
        bound = store.list_bindings(name)
        if bound:
            raise HTTPException(status_code=422, detail={
                "code": "TEMPLATE_IN_USE",
                "message": f"template {name} is bound to dataset(s): {bound}. Unbind first.",
            })
    removed = delete_template(name, _user_dir())
    if not removed:
        raise HTTPException(status_code=404, detail=f"template not found: {name}")
    reset_gallery_cache()
    logger.info("extraction_template_deleted name=%s by=%s", name, getattr(_user, "username", None))
    return {"success": True, "data": {"name": name, "deleted": True}}


# --- per-dataset bindings (persisted in system_db) ------------------------

class BindingRequest(BaseModel):
    template: str = Field(..., min_length=1, description="template name to bind")


@router.get("/bindings/{dataset}")
async def get_binding(
    dataset: str,
    request: Request,
    _user: dict = Depends(require_role(Role.ADMIN)),
) -> dict:
    """Which template is bound to ``dataset`` (None if unbound)."""
    store = _store(request)
    bound = store.get_binding(dataset) if store is not None else None
    return {"success": True, "data": {"dataset": dataset, "template": bound}}


@router.put("/bindings/{dataset}")
async def set_binding(
    dataset: str,
    req: BindingRequest,
    request: Request,
    _user: dict = Depends(require_role(Role.ADMIN)),
) -> dict:
    """Bind ``dataset`` to a template (used automatically by /kg/build)."""
    if _find(req.template) is None:
        raise HTTPException(status_code=404, detail=f"template not found: {req.template}")
    store = _store(request)
    if store is None:
        raise HTTPException(status_code=503, detail="system_db disabled; binding unavailable")
    store.set_binding(dataset, req.template, bound_by=getattr(_user, "username", None))
    logger.info("template_bound dataset=%s template=%s by=%s",
                dataset, req.template, getattr(_user, "username", None))
    return {"success": True, "data": {"dataset": dataset, "template": req.template}}


@router.delete("/bindings/{dataset}")
async def clear_binding(
    dataset: str,
    request: Request,
    _user: dict = Depends(require_role(Role.ADMIN)),
) -> dict:
    """Remove a dataset's template binding."""
    store = _store(request)
    if store is None:
        raise HTTPException(status_code=503, detail="system_db disabled; binding unavailable")
    cleared = store.clear_binding(dataset)
    return {"success": True, "data": {"dataset": dataset, "cleared": cleared}}
