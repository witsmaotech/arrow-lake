"""W3.2/F1.6 — project_concept_graph 的隐式本体形式化为 ``ontology:`` 段。

钉三件事:
1. 真实模板全量解析(22 实体类 + 16 关系,内容照抄 description);
2. **单一事实源**:`ontology.type_pairs` 与运行时白名单
   ``relation_validator.LEGAL_TYPE_PAIRS`` 语义一致(含通配与不限动词)——
   两处独立维护必然漂移,测试钉死;
3. validator 的 type-pair 匹配支持三元通配(src/rel/dst 任一为 "*"),
   运行时软降级(相关)的原始动词在捕获层保留(门禁度量抽取原始质量)。
"""

from __future__ import annotations

from pathlib import Path

import yaml

TEMPLATE_PATH = (
    Path(__file__).resolve().parents[3]
    / "arrow_lake/knowledge_graph/templates/project_concept_graph.yaml"
)


def _spec():
    from arrow_lake.ontology.template_adapter import adapt_template

    template = yaml.safe_load(TEMPLATE_PATH.read_text(encoding="utf-8"))
    return adapt_template(template)


# --- ① 真实模板全量解析 -----------------------------------------------------


def test_entity_type_enum_has_22_classes() -> None:
    spec = _spec()
    assert len(spec.entity_type_enum) == 22
    # 抽查 description 里的类名(值 = 括号前的类名)
    for t in ("主体", "硬件", "模型", "指标", "金额", "安全", "业务流程"):
        assert t in spec.entity_type_enum, f"{t} missing"


def test_relation_type_enum_has_16_verbs() -> None:
    spec = _spec()
    assert len(spec.relation_type_enum) == 16
    for v in ("包含", "属于", "依赖", "提供", "部署", "部署于", "遵循"):
        assert v in spec.relation_type_enum, f"{v} missing"


def test_required_and_warn_fields() -> None:
    spec = _spec()
    assert spec.required_entity_fields == ("name", "type", "definition")
    assert spec.required_relation_fields == ("source", "target", "type", "description")
    # definition 缺失 → warn 级观察(历史 definition 填充率曾为 0,enforce 不应
    # 因它大面积拒);类型枚举越界才是 reject。
    assert "definition" in spec.warn_fields
    assert "description" in spec.warn_fields


# --- ② 单一事实源:与运行时白名单一致 ---------------------------------------


def _runtime_pairs() -> set[tuple[str, str, str]]:
    """LEGAL_TYPE_PAIRS 展开 + 不限动词(包含/属于/依赖)按全通配。"""
    from arrow_lake.knowledge_graph.relation_validator import LEGAL_TYPE_PAIRS

    triples: set[tuple[str, str, str]] = set()
    for verb, pairs in LEGAL_TYPE_PAIRS.items():
        for src, dst in pairs:
            triples.add((src, verb, dst))
    spec_enum = _spec().relation_type_enum
    for verb in spec_enum:
        if verb not in LEGAL_TYPE_PAIRS:
            triples.add(("*", verb, "*"))  # 白名单未约束的动词 = 不限
    return triples


def test_template_pairs_match_runtime_whitelist() -> None:
    spec = _spec()
    assert set(spec.type_pairs) == _runtime_pairs(), (
        "ontology.type_pairs 与 relation_validator.LEGAL_TYPE_PAIRS 漂移 —— "
        "两处必须同源(改一处须同步另一处)"
    )


def test_constrained_verbs_present() -> None:
    spec = _spec()
    by_verb: dict[str, set[tuple[str, str, str]]] = {}
    for s, r, d in spec.type_pairs:
        by_verb.setdefault(r, set()).add((s, r, d))
    # 报价/部署于/交付于/遵循 是通配目标的代表
    assert ("*", "报价", "金额") in by_verb["报价"]
    assert ("*", "部署于", "区域") in by_verb["部署于"]
    # 不限动词按 [*, verb, *] 编码
    assert ("*", "包含", "*") in by_verb["包含"]


# --- ③ validator 三元通配 ---------------------------------------------------


def _shapes():
    from arrow_lake.ontology.shape_builder import build_shapes

    return build_shapes(_spec())


def test_src_wildcard_pair_legal() -> None:
    """报价:* → 金额 合法(源端通配)。"""
    from arrow_lake.ontology.validator import validate_snapshot

    entities = [{"name": "A", "type": "硬件"}, {"name": "P", "type": "金额"}]
    relations = [{"source": "A", "type": "报价", "target": "P"}]
    violations = [v for v in validate_snapshot(entities, relations, _shapes())
                  if v.path == "type" and "not allowed" in v.message]
    assert violations == [], "src 通配对 (*,报价,金额) 应合法"


def test_illegal_pair_flagged() -> None:
    """训练:主体 → 数据 非法(只允许 模型→数据/模型)。"""
    from arrow_lake.ontology.validator import LEVEL_REJECT, validate_snapshot

    entities = [{"name": "A", "type": "主体"}, {"name": "D", "type": "数据"}]
    relations = [{"source": "A", "type": "训练", "target": "D"}]
    hits = [v for v in validate_snapshot(entities, relations, _shapes())
            if "not allowed" in v.message]
    assert hits and hits[0].level == LEVEL_REJECT


def test_unrestricted_verb_any_pair_legal() -> None:
    """包含(白名单未约束)任意类型对合法。"""
    from arrow_lake.ontology.validator import validate_snapshot

    entities = [{"name": "A", "type": "金额"}, {"name": "B", "type": "安全"}]
    relations = [{"source": "A", "type": "包含", "target": "B"}]
    violations = [v for v in validate_snapshot(entities, relations, _shapes())
                  if "not allowed" in v.message]
    assert violations == []


def test_out_of_enum_relation_type_flagged() -> None:
    """关系动词越 16 类枚举(如 LLM 自创"使用")→ reject(sh:in 层)。"""
    from arrow_lake.ontology.validator import LEVEL_REJECT, validate_snapshot

    entities = [{"name": "A", "type": "软件"}, {"name": "B", "type": "硬件"}]
    relations = [{"source": "A", "type": "使用", "target": "B"}]
    hits = [v for v in validate_snapshot(entities, relations, _shapes())
            if v.path == "type" and v.level == LEVEL_REJECT]
    assert hits, "越枚举关系动词必须 reject"


# --- ④ 捕获层保留降级原始动词 -----------------------------------------------


def test_insert_kg_capture_keeps_original_verb() -> None:
    """运行时软降级(相关)的关系,捕获须记 original_relation_type —— 门禁
    度量的是抽取原始质量,不是修正后的结果。"""
    import asyncio
    from unittest.mock import AsyncMock

    from arrow_lake.config import HugeGraphConfig
    from arrow_lake.knowledge_graph.builder import KGBuilder, KGBuildTask
    from arrow_lake.knowledge_graph.extractor import (
        ExtractedEntity,
        ExtractedRelation,
        ExtractionResult,
    )

    client = AsyncMock()
    client.add_vertices = AsyncMock(
        side_effect=lambda vertices, **kw: [f"hg-{i}" for i in range(len(vertices))]
    )
    client.add_edges = AsyncMock(side_effect=lambda edges, **kw: len(edges))
    builder = KGBuilder(
        client, AsyncMock(),
        HugeGraphConfig(enabled=True, host="localhost", port=8089,
                        graph_name="g", build_batch_size=10),
    )
    task = KGBuildTask(
        task_id="t", status="RUNNING", dataset_name="ds", total_chunks=1,
        processed_chunks=0, entity_count=0, relation_count=0,
        started_at=None, completed_at=None, error=None,
    )
    result = ExtractionResult(
        entities=(ExtractedEntity(name="A", entity_type="主体"),),
        relations=(
            # 已被 filter_relations_by_type_pair 降级的关系:verb=相关,
            # 原始动词在 properties.original_relation_type
            ExtractedRelation(
                source="A", target="M", relation_type="相关",
                properties=(("original_relation_type", "训练"),
                            ("weight", 0.4)),
            ),
            ExtractedRelation(source="A", target="B", relation_type="包含"),
        ),
        raw_text="",
    )
    asyncio.run(builder._insert_kg(result, "kg_ds", {"c1": "1:c1"}, task=task))
    by_target = {r["target"]: r["type"] for r in task.inserted_relations}
    assert by_target["M"] == "训练", "降级关系须捕获原始动词(训练)"
    assert by_target["B"] == "包含"
