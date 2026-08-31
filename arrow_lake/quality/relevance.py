"""F5.2 — 相关性抽样评估回路(v1.11.4 MS5 W2.1,设计 §3)。

复用 MS4 标注闭环(S2,零新回收设施):三分类 Choices 项目注册进
``annotation_projects`` → **既有 30s scheduler 自动回收**(Choices →
ADL 的 scenario 字段)→ ``compute_relevance`` 聚合。本模块只补两块:

1. **回路编排**:relevance 项目建/复用(名字约定 ``{ds}__relevance``)+
   LS 懒绑定 + 抽样行派发(LLM choices 预标注,人工复核);
2. **LLM 直评降级**(``llm_only``):标注者缺位时 LLM 判定直接落 ADL
   (annotator_id=``llm:<model>``)——建议非结论,报告侧 source=llm
   降级标记(设计 §3 降级档)。

反哺(评估后整改建议)在 W4 console 消费 ADL,不在本模块。
"""

from __future__ import annotations

import asyncio
import contextlib
import uuid
from datetime import UTC, datetime
from typing import Any

from arrow_lake.quality.dimensions import RELEVANCE_LABELS

__all__ = [
    "RELEVANCE_LS_CONFIG",
    "RELEVANCE_PROJECT_SUFFIX",
    "RELEVANCE_SAMPLE_CAP",
    "classify_relevance",
    "dispatch_relevance",
    "llm_only_relevance",
    "relevance_project_name",
]

#: LS labeling config:三分类 Choices(D2;Choices value 即回收后的 scenario)。
RELEVANCE_LS_CONFIG = """<View>
  <Text name="text" value="$text"/>
  <Choices name="relevance" toName="text" required="true" showInLine="true">
    <Choice value="高相关"/>
    <Choice value="间接相关"/>
    <Choice value="不相关"/>
  </Choices>
</View>"""

#: 抽样帽(D2:默认 500 cap)。
RELEVANCE_SAMPLE_CAP = 500
RELEVANCE_PROJECT_SUFFIX = "__relevance"

_CLASSIFY_SYSTEM_PROMPT = (
    "你是数据集相关性评审员。判断给定文本与其所属数据集主题的相关性。"
    "只输出以下三个选项之一,不要输出任何其他内容:"
    "高相关 / 间接相关 / 不相关"
)


def relevance_project_name(dataset: str) -> str:
    """relevance 项目名约定(与 L4 项目共享注册表,靠后缀区分)。"""
    return f"{dataset}{RELEVANCE_PROJECT_SUFFIX}"


async def classify_relevance(provider: Any, text: str) -> str | None:
    """LLM 三分类初判;输出不含任何合法标签 → None(宁缺勿错)。"""
    from arrow_lake.rag.provider import LLMMessage

    resp = await provider.generate([
        LLMMessage(role="system", content=_CLASSIFY_SYSTEM_PROMPT),
        LLMMessage(role="user", content=text[:4000]),
    ])
    content = (getattr(resp, "content", "") or "").strip()
    for label in sorted(RELEVANCE_LABELS, key=len, reverse=True):
        if label in content:
            return label
    return None


def _choices_prediction(label: str | None, model: str) -> dict[str, Any]:
    """LS 静态 prediction(choices 形态;label=None → 空建议)。"""
    result: list[dict[str, Any]] = []
    if label is not None:
        result.append({
            "from_name": "relevance", "to_name": "text", "type": "choices",
            "value": {"choices": [label]},
        })
    return {"model_version": f"llm-relevance:{model}", "result": result}


def _masked_text(
    text: str,
    generalize_rules: tuple[tuple[str, str], ...] = (),
    entity_names: tuple[str, ...] = (),
) -> str:
    from arrow_lake.annotation.masking import apply_annotation_masking

    return apply_annotation_masking(
        text, generalize_rules=generalize_rules,
        entity_names=entity_names, hmac_key=None)


async def dispatch_relevance(
    *,
    ls_client: Any,
    project_title: str,
    rows: list[dict[str, Any]],
    text_column: str,
    provider: Any,
    ls_project_id: int | None,
    bind_ls_project: Any = None,
    import_batch_size: int = 50,
    generalize_rules: tuple[tuple[str, str], ...] = (),
    entity_names: tuple[str, ...] = (),
) -> dict[str, Any]:
    """抽样行(调用方已抽)→ 脱敏 → LLM 初判 → LS import(choices 预标)。

    LS 懒绑定沿 run_dispatch 纪律:仅 404 重建,瞬时错误上抛(孤儿化
    防护)。单行 LLM 失败 → 空 prediction 仍派发(预标注是建议非强制)。
    """
    from arrow_lake.annotation.dispatch import LSClientError, stable_row_id

    if ls_project_id is not None:
        try:
            ls_client.get_project(ls_project_id)
        except LSClientError as exc:
            if getattr(exc, "status", None) != 404:
                raise
            ls_project_id = None
    if ls_project_id is None:
        rec = ls_client.create_project(project_title, RELEVANCE_LS_CONFIG)
        ls_project_id = int(rec.get("id", 0))
        if not ls_project_id:
            raise LSClientError("LS create_project returned no id")
        if bind_ls_project is not None:
            bind_ls_project(ls_project_id)

    model = getattr(provider, "model", "llm") if provider is not None else "llm"
    tasks: list[dict[str, Any]] = []
    # M16(四维 review):预标注 LLM 并发——此前逐行串行,500 行 ≈ 12-25 分钟
    sem = asyncio.Semaphore(8)

    async def _one(i: int, row: dict[str, Any]) -> dict[str, Any] | None:
        text = str(row.get(text_column) or "").strip()
        if not text:
            return None
        masked = _masked_text(text, generalize_rules, entity_names)
        label: str | None = None
        if provider is not None:
            async with sem:
                with contextlib.suppress(Exception):
                    label = await classify_relevance(provider, masked)
        return {
            "data": {
                "text": masked,
                "row_id": stable_row_id(text, i),
                "strategy": "relevance",
            },
            "predictions": [_choices_prediction(label, model)],
            "_pre": label is not None,
        }

    out = [t for t in await asyncio.gather(
        *[_one(i, r) for i, r in enumerate(rows)]) if t is not None]
    tasks = [{k: v for k, v in t.items() if k != "_pre"} for t in out]
    skipped = len(rows) - len(tasks)
    preannotated = sum(1 for t in out if t["_pre"])

    for start in range(0, len(tasks), max(1, import_batch_size)):
        await _maybe_async(ls_client.import_tasks(
            ls_project_id, tasks[start:start + max(1, import_batch_size)]))
    return {
        "ls_project_id": ls_project_id, "dispatched": len(tasks),
        "skipped": skipped, "preannotated": preannotated,
    }


async def llm_only_relevance(
    *, lake: Any, dataset: str, rows: list[dict[str, Any]],
    text_column: str, provider: Any,
    generalize_rules: tuple[tuple[str, str], ...] = (),
    entity_names: tuple[str, ...] = (),
) -> dict[str, Any]:
    """LLM 直评降级:判定直接写 ADL(annotator_id=llm:<model>)。

    幂等由 ADL adl_id 内容键承担(build_adl_batch;同文本同判定同 id →
    重跑零新行)。κ 一致性侧已排除 ``llm:`` 标注者(dimensions)。
    """
    from arrow_lake._lake_ingest import _DeadLetterStorageAdapter
    from arrow_lake.annotation.adl import build_adl_batch, write_adl
    from arrow_lake.annotation.dispatch import stable_row_id
    from arrow_lake.annotation.recover import RecoveredAnnotation
    from arrow_lake.annotation.sync import _existing_adl_state

    model = getattr(provider, "model", "llm")
    annotator = f"llm:{model}"
    now = datetime.now(tz=UTC).isoformat()
    # M16:LLM 直评并发(同 dispatch_relevance)
    sem = asyncio.Semaphore(8)

    async def _one(i: int, row: dict[str, Any]) -> RecoveredAnnotation | None:
        text = str(row.get(text_column) or "").strip()
        if not text:
            return None
        masked = _masked_text(text, generalize_rules, entity_names)
        async with sem:
            try:
                label = await classify_relevance(provider, masked)
            except Exception:
                label = None
        if label is None:
            return None
        return RecoveredAnnotation(
            task_id=0, row_id=stable_row_id(text, i), strategy="relevance",
            annotator_id=annotator, annotated_at=now, ground_truth=False,
            objects=(), events=(), relations=(), rules_applied=(),
            scenario=label,
        )

    results = await asyncio.gather(*[_one(i, r) for i, r in enumerate(rows)])
    recs = [r for r in results if r is not None]
    failed = len(results) - len(recs)

    written = 0
    if recs:
        existing_ids, group_versions = _existing_adl_state(lake, dataset)
        table, written = build_adl_batch(
            dataset=dataset, recovered=recs, adjudications={},
            batch_id=f"rel-{uuid.uuid4().hex[:8]}",
            existing_adl_ids=existing_ids, group_versions=group_versions,
        )
        if written:
            write_adl(_DeadLetterStorageAdapter(lake._get_storage()), dataset, table)
    return {
        "annotator": annotator, "assessed": len(recs),
        "failed": failed, "adl_rows_written": written,
    }


async def _maybe_async(value: Any) -> Any:
    import inspect

    if inspect.isawaitable(value):
        return await value
    return value
