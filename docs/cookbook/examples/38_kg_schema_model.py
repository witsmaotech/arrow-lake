#!/usr/bin/env python3
"""38 — KG Schema 与数据模型

场景: 深入了解知识图谱的 Schema 定义，包括顶点标签、边标签、
属性键和索引标签。展示 ARROW_LAKE_KG_SCHEMA 的完整结构。

数据: 内部数据模型 (无需外部服务)
"""

from __future__ import annotations

import shutil
import sys
from pathlib import Path

BASE_URI = "./_tmp_kg_schema"


def main() -> None:
    no_cleanup = "--no-cleanup" in sys.argv
    print("=" * 60)
    print("38 KG Schema 与数据模型")
    print("=" * 60)

    base = Path(BASE_URI)
    if base.exists():
        shutil.rmtree(base)

    # STEP 1: 查看 GraphSchema
    print("STEP 1: Arrow Lake KG Schema 定义")
    from arrow_lake.knowledge_graph.schema import (
        ARROW_LAKE_KG_SCHEMA, GraphSchema,
        schema_to_hugegraph_payload,
        PropertyKeyDef, VertexLabelDef, EdgeLabelDef, IndexLabelDef,
    )
    schema = ARROW_LAKE_KG_SCHEMA

    print(f"\n  属性键 (Property Keys): {len(schema.property_keys)} 个")
    for pk in schema.property_keys:
        print(f"    {pk.name:<20} {pk.data_type:<12} cardinality={pk.cardinality}")

    print(f"\n  顶点标签 (Vertex Labels): {len(schema.vertex_labels)} 个")
    for vl in schema.vertex_labels:
        props = ", ".join(vl.properties) if vl.properties else "(无属性)"
        print(f"    {vl.name:<20} pk={vl.primary_keys} id={vl.id_strategy} [{props}]")

    print(f"\n  边标签 (Edge Labels): {len(schema.edge_labels)} 个")
    for el in schema.edge_labels:
        props = ", ".join(el.properties) if el.properties else "(无属性)"
        print(f"    {el.name:<24} {el.source_label} → {el.target_label} [{props}]")

    print(f"\n  索引标签 (Index Labels): {len(schema.index_labels)} 个")
    for il in schema.index_labels:
        print(f"    {il.name:<24} {il.base_type} on {il.base_value} [{', '.join(il.fields)}]")

    # STEP 2: 转换为 HugeGraph payload
    print("\nSTEP 2: HugeGraph REST API Payload")
    payload = schema_to_hugegraph_payload(schema)
    for key in ["property_keys", "vertex_labels", "edge_labels", "index_labels"]:
        items = payload.get(key, [])
        print(f"  {key}: {len(items)} 项")

    # STEP 3: 数据模型
    print("\nSTEP 3: 核心数据模型")

    from arrow_lake.knowledge_graph.extractor import (
        ExtractedEntity, ExtractedRelation, ExtractionResult,
    )
    print("\n  ExtractedEntity (实体):")
    print("    name: str           实体名称")
    print("    entity_type: str      实体类型 (person/org/concept/location/...)")
    print("    properties: tuple      额外属性键值对")

    print("\n  ExtractedRelation (关系):")
    print("    source: str          源实体名称")
    print("    target: str          目标实体名称")
    print("    relation_type: str    关系类型")
    print("    properties: tuple      额外属性键值对")

    print("\n  ExtractionResult (抽取结果):")
    print("    entities: tuple      提取到的实体列表")
    print("    relations: tuple     提取到的关系列表")
    print("    raw_text: str        原始文本")

    # STEP 4: Graph Retrieval 数据模型
    print("\nSTEP 4: 图谱检索数据模型")
    from arrow_lake.knowledge_graph.retriever import (
        GraphTriplet, GraphRetrievalResult,
    )

    print("\n  GraphTriplet (知识三元组):")
    print("    subject: str         主语实体")
    print("    predicate: str        谓词 (关系)")
    print("    object_: str          宾语实体")
    print("    properties: tuple     额外属性")

    print("\n  GraphRetrievalResult (检索结果):")
    print("    query_entities: tuple   查询中识别的实体")
    print("    triplets: tuple         检索到的三元组")
    print("    traversal_depth: int    遍历深度")
    print("    vertex_count: int      顶点数")
    print("    edge_count: int        边数")

    # STEP 5: RAG Response 模型
    print("\nSTEP 5: RAG Response 模型")
    from arrow_lake.rag.pipeline import RAGResponse
    print("    answer: str           LLM 生成的回答")
    print("    citations: tuple        引用列表 (RAGCitation)")
    print("    retrieval_count: int    检索到的文档数")
    print("    context_tokens: int     上下文 token 数")
    print("    llm_usage: dict         LLM 用量统计")
    print("    latency_ms: float       响应延迟 (毫秒)")
    print("    session_id: str|None    会话 ID")

    # STEP 6: Gremlin 查询模板
    print("\nSTEP 6: Gremlin 查询模板 (GremlinQueries)")
    from arrow_lake.knowledge_graph.queries import GremlinQueries

    queries = [
        ("find_entity", "按名称查找实体"),
        ("get_neighbors", "多跳邻居遍历"),
        ("shortest_path", "最短路径"),
        ("get_subgraph", "子图提取"),
        ("entity_type_counts", "实体类型统计"),
        ("traverse_from_entities", "多实体联合遍历"),
    ]
    for name, desc in queries:
        print(f"    {name:<28} {desc}")

    # STEP 7: 配置模型
    print("\nSTEP 7: KG 配置模型 (HugeGraphConfig)")
    from arrow_lake.config.rag import HugeGraphConfig
    cfg = HugeGraphConfig()
    print(f"    enabled: {cfg.enabled}")
    print(f"    host: {cfg.host}")
    print(f"    port: {cfg.port}")
    print(f"    graph_name: {cfg.graph_name}")
    print(f"    timeout: {cfg.timeout_seconds}s")
    print(f"    default_traversal_depth: {cfg.default_traversal_depth}")
    print(f"    max_traversal_depth: {cfg.max_traversal_depth}")
    print(f"    build_batch_size: {cfg.build_batch_size}")
    print(f"    auto_build_on_ingest: {cfg.auto_build_on_ingest}")

    print("\n  [全部 PASS]")
    shutil.rmtree(base, ignore_errors=True)
    print("(已清理)")


if __name__ == "__main__":
    main()
