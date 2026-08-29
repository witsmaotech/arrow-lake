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

from typing import Any

from arrow_lake.annotation.template_gen import EVENT_KEYWORDS
from arrow_lake.knowledge_graph.extractor import ExtractionResult

__all__ = ["to_ls_prediction"]


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
