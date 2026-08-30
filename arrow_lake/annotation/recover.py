"""F4.5(回收侧)— LS task/annotation 解析 + webhook + watermark(v1.11.3 W3.1)。

旁路纯解析:LS task JSON(REST list_tasks / webhook payload 同构)→
五段结构化标注记录。轮询对账为(S9)主通道(webhook 只加速):watermark
= 已回收的最大 LS task id,增量拉取 ``task.id > watermark`` 且带标注的
任务,幂等去重交给 ADL(adl_id)。

annotation result 与 prediction result 同构(labels regions / choices /
textarea / relation)。relation 的 from_id/to_id 引 region id,反查
``value.text`` 还原主宾文本;引用缺失(预标注漂移/手删)→ 丢弃该条,
宁缺勿错。skip(was_cancelled)与空 result 的标注不回收。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

__all__ = [
    "RecoveredAnnotation",
    "incremental_tasks",
    "parse_ls_annotation",
    "parse_webhook",
]

_SPAN_FIELDS = ("label", "start", "end", "text")


@dataclass(frozen=True)
class Span:
    label: str
    start: int
    end: int
    text: str


@dataclass(frozen=True)
class Triple:
    subject: str
    predicate: str
    object: str


@dataclass(frozen=True)
class RecoveredAnnotation:
    """一条已回收的人工标注(五段 + 元数据;ADL 行的数据面)。"""

    task_id: int
    row_id: str
    strategy: str
    annotator_id: str
    annotated_at: str
    ground_truth: bool
    objects: tuple[Span, ...]
    events: tuple[Span, ...]
    relations: tuple[Triple, ...]
    rules_applied: tuple[str, ...]
    scenario: str


def _parse_one(
    task: dict[str, Any], annotation: dict[str, Any]
) -> RecoveredAnnotation | None:
    if annotation.get("was_cancelled"):
        return None
    result = annotation.get("result") or []
    if not result:
        return None

    objects: list[Span] = []
    events: list[Span] = []
    relations: list[Triple] = []
    rules: list[str] = []
    scenario = ""
    text_of: dict[str, str] = {}

    for item in result:
        rtype = item.get("type")
        value = item.get("value") or {}
        if rtype == "labels":
            from_name = item.get("from_name", "")
            text = str(value.get("text") or "")
            labels = value.get("labels") or []
            label = str(labels[0]) if labels else ""
            # review P3: 非整数 offset(脏数据/恶意 payload)丢该 region,
            # 不让一条坏标注 500 整个回收
            try:
                span = Span(
                    label, int(value.get("start", 0)), int(value.get("end", 0)), text)
            except (TypeError, ValueError):
                continue
            if item.get("id"):
                text_of[str(item["id"])] = text
            (events if from_name == "events" else objects).append(span)
        elif rtype == "choices":
            choices = value.get("choices") or []
            scenario = str(choices[0]) if choices else ""
        elif rtype == "textarea":
            raw = value.get("text") or []
            if isinstance(raw, str):
                raw = [raw]
            rules.extend(str(line) for chunk in raw for line in str(chunk).splitlines() if line)
        elif rtype == "relation":
            labels = item.get("labels") or []
            predicate = str(labels[0]) if labels else ""
            subject = text_of.get(str(item.get("from_id")), "")
            obj = text_of.get(str(item.get("to_id")), "")
            if subject and obj and predicate:
                relations.append(Triple(subject, predicate, obj))

    data = task.get("data") or {}
    try:
        task_id = int(task.get("id", 0))
    except (TypeError, ValueError):
        task_id = 0
    return RecoveredAnnotation(
        task_id=task_id,
        row_id=str(data.get("row_id") or f"task-{task.get('id', 0)}"),
        strategy=str(data.get("strategy") or ""),
        annotator_id=str(annotation.get("completed_by", "")),
        annotated_at=str(annotation.get("created_at") or ""),
        ground_truth=bool(annotation.get("ground_truth")),
        objects=tuple(objects),
        events=tuple(events),
        relations=tuple(relations),
        rules_applied=tuple(rules),
        scenario=scenario,
    )


def parse_ls_annotation(task: dict[str, Any]) -> list[RecoveredAnnotation]:
    """一个 LS task → 多标注者各一条(skip/空 result 丢弃)。"""
    out = []
    for annotation in task.get("annotations") or []:
        rec = _parse_one(task, annotation)
        if rec is not None:
            out.append(rec)
    return out


def parse_webhook(payload: dict[str, Any]) -> RecoveredAnnotation | None:
    """webhook payload → 单条标注;非标注事件或缺字段 → None。"""
    action = str(payload.get("action") or "")
    if action not in ("ANNOTATION_CREATED", "ANNOTATION_UPDATED"):
        return None
    annotation = payload.get("annotation")
    task = payload.get("task")
    if not isinstance(annotation, dict) or not isinstance(task, dict):
        return None
    return _parse_one(task, annotation)


def incremental_tasks(
    tasks: list[dict[str, Any]], *, watermark: int
) -> tuple[list[dict[str, Any]], int]:
    """watermark 增量:**仅带标注的任务**推进 watermark(W5 live 实证修)。

    标注晚于 task 创建——若按 task id 无条件推进,后到的标注会永远落在
    增量窗口外(scheduler 曾在标注发生前把 watermark 推满,ADL 空)。
    现语义:``id > watermark 且 annotations 非空`` 才构成增量;无标注的
    pending task 留在窗口内反复重拉(页帽 200 保护成本),被标注后即
    回收。已知限制(登记):已回收 task 的**重标注**(id ≤ watermark)
    轮询模型捕获不了——webhook ANNOTATION_UPDATED 是补丁通道。
    """
    def _tid(t: dict[str, Any]) -> int:
        try:
            return int(t.get("id", 0))
        except (TypeError, ValueError):  # review P3: 脏 id 视为 0(不阻断)
            return 0

    fresh = [
        t for t in tasks
        if _tid(t) > watermark and t.get("annotations")
    ]
    new_wm = max((_tid(t) for t in fresh), default=watermark)
    return fresh, new_wm
