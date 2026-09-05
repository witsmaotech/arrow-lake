"""W2.2 — annotation/preannotate:HE 抽取结果 → LS predictions(设计 §5 / S4)。

契约:
* 实体 → labels region(start/end 为**字符 offset**;name 在文本中首次
  出现定位,找不到跳过——prediction 只标可定位的);
* 事件类实体(命中 EVENT_KEYWORDS,与 template_gen 同源)分流到
  ``from_name="events"``,其余 ``objects``;
* relations:source/target 实体都已定位 → relation 条目引用 region id;
  端点缺失(文本中无)→ 跳过;
* 静态 prediction 包装(S4):{"model_version": "hyper-extract", "result":
  [...]},无在线推理;
* 空结果 → result 空;同名实体只标首个出现。
"""

from __future__ import annotations

from arrow_lake.annotation.preannotate import to_ls_prediction
from arrow_lake.knowledge_graph.extractor import (
    ExtractedEntity,
    ExtractedRelation,
    ExtractionResult,
)

TEXT = "芜湖市应急管理局对凤凰花园小区燃气泄漏事故展开处置,调压站压力异常。"


def _result(entities, relations=()):
    return ExtractionResult(
        entities=tuple(entities), relations=tuple(relations), raw_text=TEXT
    )


class TestEntitiesToRegions:
    def test_object_entity_region_offsets(self):
        res = _result([ExtractedEntity("调压站", "硬件")])
        pred = to_ls_prediction(res)
        region = pred["result"][0]
        assert region["type"] == "labels"
        assert region["from_name"] == "objects"
        assert region["to_name"] == "text"
        assert region["value"]["text"] == "调压站"
        assert TEXT[region["value"]["start"]:region["value"]["end"]] == "调压站"

    def test_event_entity_routed_to_events(self):
        res = _result([ExtractedEntity("燃气泄漏事故", "事故")])
        region = to_ls_prediction(res)["result"][0]
        assert region["from_name"] == "events"
        assert region["value"]["labels"] == ["事故"]

    def test_entity_not_in_text_skipped(self):
        res = _result([
            ExtractedEntity("不存在的实体", "组织"),
            ExtractedEntity("调压站", "硬件"),
        ])
        regions = to_ls_prediction(res)["result"]
        assert len(regions) == 1
        assert regions[0]["value"]["text"] == "调压站"

    def test_duplicate_entity_name_first_occurrence_only(self):
        text = "阀门A与阀门A相连"  # 同名两次出现
        res = ExtractionResult(
            entities=(ExtractedEntity("阀门A", "硬件"),),
            relations=(), raw_text=text,
        )
        regions = to_ls_prediction(res)["result"]
        assert len(regions) == 1
        assert regions[0]["value"]["start"] == 0  # 首次出现


class TestRelations:
    def test_relation_with_located_endpoints(self):
        res = _result(
            [ExtractedEntity("调压站", "硬件"), ExtractedEntity("燃气泄漏事故", "事故")],
            [ExtractedRelation("调压站", "燃气泄漏事故", "导致")],
        )
        pred = to_ls_prediction(res)
        rel = next(r for r in pred["result"] if r["type"] == "relation")
        assert rel["from_id"] and rel["to_id"]
        assert rel["from_id"] != rel["to_id"]
        assert rel["labels"] == ["导致"]
        assert rel["direction"] == "right"
        # from_id/to_id 引用的 region 真实存在
        ids = {r["id"] for r in pred["result"] if r["type"] == "labels"}
        assert rel["from_id"] in ids and rel["to_id"] in ids

    def test_relation_endpoint_missing_skipped(self):
        res = _result(
            [ExtractedEntity("调压站", "硬件")],
            [ExtractedRelation("调压站", "幽灵实体", "导致")],  # 幽灵端点未定位
        )
        assert to_ls_prediction(res)["result"] == [
            r for r in to_ls_prediction(res)["result"] if r["type"] != "relation"
        ]
        assert all(r["type"] == "labels" for r in to_ls_prediction(res)["result"])


class TestWrapper:
    def test_prediction_shape_static_model_version(self):
        pred = to_ls_prediction(_result([ExtractedEntity("调压站", "硬件")]))
        assert pred["model_version"] == "hyper-extract"
        assert isinstance(pred["result"], list)

    def test_empty_result_yields_empty(self):
        assert to_ls_prediction(_result([]))["result"] == []
        assert to_ls_prediction(ExtractionResult(entities=(), relations=(), raw_text=""))["result"] == []


# --------------------------------------------------------------------------- #
# W2 #6 首跑发现(2026-09-05):精简 config + NER 预测 → 悬空 relation 崩 LS
# --------------------------------------------------------------------------- #


def test_filter_prediction_to_config_choices_only_drops_ner_and_relations():
    """Choices-only config:NER region(objects/events 控件不存在)与依赖
    它们的 relation 全部丢弃——LS 端不会再出现悬空引用。"""
    from arrow_lake.annotation.preannotate import filter_prediction_to_config

    pred = {"model_version": "hyper-extract", "result": [
        {"id": "e0", "from_name": "objects", "to_name": "text", "type": "labels",
         "value": {"start": 0, "end": 2, "text": "钢瓶", "labels": ["设备"]}},
        {"id": "e1", "from_name": "events", "to_name": "text", "type": "labels",
         "value": {"start": 3, "end": 5, "text": "泄漏", "labels": ["事件"]}},
        {"id": "r2", "type": "relation", "from_id": "e0", "to_id": "e1",
         "direction": "right", "labels": ["导致"]},
    ]}
    cfg = ('<View><Text name="text" value="$text"/>'
           '<Choices name="relevance" toName="text" required="true">'
           '<Choice value="高相关"/></Choices></View>')
    out = filter_prediction_to_config(pred, cfg)
    assert out["result"] == []


def test_filter_prediction_to_config_keeps_matching_controls():
    """控件匹配(Labels objects/events + Choices)全保留;relation 端点
    存活才保留,端点被丢则连坐丢弃。"""
    from arrow_lake.annotation.preannotate import filter_prediction_to_config

    pred = {"model_version": "hyper-extract", "result": [
        {"id": "e0", "from_name": "objects", "to_name": "text", "type": "labels",
         "value": {"start": 0, "end": 2, "text": "钢瓶", "labels": ["设备"]}},
        {"id": "e9", "from_name": "ghost", "to_name": "text", "type": "labels",
         "value": {"start": 0, "end": 2, "text": "x", "labels": ["y"]}},
        {"id": "r1", "type": "relation", "from_id": "e0", "to_id": "e9",
         "direction": "right", "labels": ["导致"]},
        {"id": "r2", "type": "relation", "from_id": "e0", "to_id": "e0",
         "direction": "right", "labels": ["自环"]},
    ]}
    cfg = ('<View><Text name="text" value="$text"/>'
           '<Labels name="objects" toName="text"><Label value="设备"/></Labels>'
           '<Labels name="events" toName="text"><Label value="事件"/></Labels></View>')
    out = filter_prediction_to_config(pred, cfg)
    ids = [i["id"] for i in out["result"]]
    assert "e0" in ids and "e9" not in ids
    assert "r1" not in ids  # to_id e9 被丢 → relation 连坐
    assert "r2" in ids      # 端点 e0 存活(自环语义由 LS 处置,保真不裁)
