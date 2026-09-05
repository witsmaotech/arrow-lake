"""F4.4 — hyper-extract 抽取结果 → LS 静态 predictions(v1.11.3 MS4 W2.2)。

旁路纯映射:不调 LLM(LLM 抽取在 dispatch 运行时经 he_extractor 发生,
产物 :class:`ExtractionResult` 进这里转 LS prediction JSON)。静态
predictions 随 task import 内嵌(S4 红线:无 ML backend,标注者可修改)。

HE 实体**无字符 offset**(``ExtractedEntity`` 只有 name/type)——region
定位 = name 在文本中的首次出现(``str.find``);找不到则跳过(prediction
只能标可定位的,宁缺勿错)。事件类分流与 config 生成同源
(:data:`template_gen.EVENT_KEYWORDS`)。
"""

from __future__ import annotations

import re
from typing import Any

from arrow_lake.annotation.template_gen import EVENT_KEYWORDS
from arrow_lake.knowledge_graph.extractor import ExtractionResult

__all__ = ["filter_prediction_to_config", "to_ls_prediction"]

# LS 控件标签(config XML 里带 name= 的交互控件;非控件标签如 View/Text/
# Header 无 from_name 语义,不计)
_CONTROL_TAGS = ("Choices", "Labels", "Rating", "Pairwise", "Ranker", "Taxonomy",
                 "TextArea", "Brush", "KeyPoint")
_CONTROL_NAME_RE = re.compile(
    r"<(?:" + "|".join(_CONTROL_TAGS) + r")\b[^>]*\bname=\"([^\"]+)\""
)


def _config_control_names(labeling_config: str) -> set[str]:
    """labeling config XML 里声明的控件名(from_name 可取值集)。"""
    return set(_CONTROL_NAME_RE.findall(labeling_config or ""))


def filter_prediction_to_config(
    prediction: dict[str, Any], labeling_config: str
) -> dict[str, Any]:
    """预测结果按项目 labeling config 过滤(W2 #6 首跑发现,2026-09-05)。

    手写/精简 config(如三分类只有 ``<Choices>``)+ hyper-extract 预测带
    NER region(``from_name=objects/events``)时,LS 会**丢弃无法挂控件的
    region 但保留 relation** → 悬空引用 → 标注页 mobx-state-tree 崩
    (``Failed to resolve reference 'e0#…'``)。这里源头过滤:

    * label 型结果(config 无其 from_name 控件)→ 丢弃;
    * relation(from_id/to_id 指向被丢弃 region)→ 丢弃;
    * 过滤后空 → 空 result(无预测,LS 渲染干净)。
    """
    names = _config_control_names(labeling_config)
    if not names:
        return prediction  # 解析不出控件名(意外形态)→ 不动,宁保守
    kept: list[dict[str, Any]] = []
    alive_ids: set[str] = set()
    for item in prediction.get("result") or []:
        if item.get("type") == "relation":
            continue  # 二轮处理(端点存活性依赖 region 去留)
        if item.get("from_name") not in names:
            continue
        kept.append(item)
        if item.get("id"):
            alive_ids.add(item["id"])
    for item in prediction.get("result") or []:
        if item.get("type") != "relation":
            continue
        if item.get("from_id") in alive_ids and item.get("to_id") in alive_ids:
            kept.append(item)
    return {**prediction, "result": kept}


def _is_event(entity_type: str) -> bool:
    return any(k in entity_type for k in EVENT_KEYWORDS)


def to_ls_prediction(result: ExtractionResult, *, text: str | None = None) -> dict[str, Any]:
    """ExtractionResult → LS prediction dict(import 时内嵌进 task)。

    Returns:
        ``{"model_version": "hyper-extract", "result": [region/relation ...]}``。
    """
    body = text if text is not None else result.raw_text
    out: list[dict[str, Any]] = []
    region_of: dict[str, str] = {}  # entity name → region id(首个定位)
    idx = 0
    for entity in result.entities:
        if not entity.name or entity.name in region_of:
            continue
        start = body.find(entity.name)
        if start < 0:
            continue
        region_id = f"e{idx}"
        idx += 1
        region_of[entity.name] = region_id
        out.append({
            "id": region_id,
            "from_name": "events" if _is_event(entity.entity_type) else "objects",
            "to_name": "text",
            "type": "labels",
            "value": {
                "start": start,
                "end": start + len(entity.name),
                "text": entity.name,
                "labels": [entity.entity_type],
            },
        })
    for rel in result.relations:
        src = region_of.get(rel.source)
        dst = region_of.get(rel.target)
        if src is None or dst is None or src == dst:
            continue
        out.append({
            "id": f"r{len(out)}",
            "from_id": src,
            "to_id": dst,
            "type": "relation",
            "direction": "right",
            "labels": [rel.relation_type],
        })
    return {"model_version": "hyper-extract", "result": out}
