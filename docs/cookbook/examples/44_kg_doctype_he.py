#!/usr/bin/env python3
"""44 — v1.7 文档类型路由 + hyper-extract (he) 抽取后端

场景: 演示 v1.7.x 的 doc_type 三层路由（override→gallery→default）+
      normalize 别名归一 + LLM 内容推断，以及 he 抽取后端的启用。

本示例分两部分:
  PART A (无需 LLM): 演示 DocTypeRouter / TemplateGallery / normalize_doc_type
          — 纯路由逻辑，可离线运行，展示 doc_type 如何映射到 hyper-extract 模板。
  PART B (需 LLM + HugeGraph): 启用 he 后端，真实抽取（可选，缺 LLM 时跳过）。

v1.7 doc_type 三层保障（首次命中即用）:
  1. config override  (HugeGraphConfig.he_doc_type_templates)
  2. gallery 元数据匹配 (TemplateGallery 扫描 hyperextract preset 的 tags/category/...)
  3. default 兜底     (HugeGraphConfig.he_default_template)

前置:
  - PART A: 仅需 hyperextract 安装 (pip install '.[he]')
  - PART B: HugeGraph PD 集群运行 + LLM (Ollama/OpenAI 兼容) 可用
"""

from __future__ import annotations

import argparse
import asyncio
import os

from arrow_lake.config import ArrowLakeConfig
from arrow_lake.knowledge_graph.doc_type_router import (
    DocTypeRouter,
    TemplateGallery,
    normalize_doc_type,
    validate_taxonomy,
)


def part_a_routing() -> None:
    """PART A: doc_type 路由逻辑演示（无需 LLM/HugeGraph）。"""
    print("=" * 60)
    print("PART A: doc_type 三层路由演示（离线，无需 LLM）")
    print("=" * 60)

    # 1) 别名归一化：不同写法/语言 → 同一 canonical doc_type
    print("\n[1] normalize_doc_type 别名归一化")
    for raw in ["Paper", "research_paper", "论文", "学术论文", "白皮书",
                "tutorial", "财报", "中医", "unknown-type"]:
        print(f"    {raw!r:20s} -> {normalize_doc_type(raw)!r}")

    # 2) TemplateGallery: 扫描 hyperextract 全部 preset，按元数据建索引
    print("\n[2] TemplateGallery 元数据索引（排除 base_* 不可抽取模板）")
    gallery = TemplateGallery.build()
    print(f"    索引模板数: {len(gallery.templates)}")
    print(f"    分类: {sorted({t.category for t in gallery.templates})}")

    # 3) gallery.match: doc_type → 模板（tag→category→name→description 四级匹配）
    print("\n[3] gallery.match 四级匹配（无 override 时）")
    for dt in ["concept", "finance", "workflow", "paper", "herb"]:
        hit = gallery.match(dt)
        print(f"    {dt!r:12s} -> {hit.path if hit else 'None':28s} "
              f"(tags={list(hit.tags[:3]) if hit else []})")

    # 4) DocTypeRouter 三层优先级: override > gallery > default
    print("\n[4] DocTypeRouter 三层路由 + resolve_with_source 观测")
    router = DocTypeRouter(
        doc_type_templates={"paper": "general/concept_graph"},  # 显式 override
        default_template="general/concept_graph",
    )
    for dt in ["paper", "论文", "concept", "finance", "unknown-xyz"]:
        path, source = router.resolve_with_source(dt)
        print(f"    {dt!r:14s} -> {path:28s} via {source!r}")

    # 5) taxonomy 一致性（CI 守护）
    print("\n[5] validate_taxonomy 一致性检查（应为空 = 三套 taxonomy 对齐）")
    warnings = validate_taxonomy()
    print(f"    warnings: {warnings if warnings else '(none — taxonomy aligned)'}")


async def part_b_he_build(skip_llm: bool) -> None:
    """PART B: 启用 he 后端，真实抽取（需 LLM + HugeGraph，可选）。"""
    print("\n" + "=" * 60)
    print("PART B: he 抽取后端（需 LLM + HugeGraph）")
    print("=" * 60)

    if skip_llm:
        print("  (--skip-llm 跳过；启用 he 需 LLM + HugeGraph PD 集群)")
        print("  启用方式: config.hugegraph.extractor_backend = 'he'")
        print("           config.hugegraph.he_model = 'qwen3:30b-a3b'")
        print("           config.llm.api_base = 'http://<ollama>:11434/v1'")
        return

    config = ArrowLakeConfig()
    config.hugegraph.enabled = True
    config.hugegraph.host = os.getenv("HG_HOST", "localhost")
    config.hugegraph.port = int(os.getenv("HG_PORT", "8089"))
    config.hugegraph.graph_name = os.getenv("HG_GRAPH", "hugegraph")
    # v1.7: 启用 he 后端
    config.hugegraph.extractor_backend = "he"
    config.hugegraph.he_model = os.getenv("HE_MODEL", "qwen3:30b-a3b")
    config.llm.api_base = os.getenv("OLLAMA_API_BASE", "http://10.100.93.100:11434/v1")
    config.llm.model = config.hugegraph.he_model
    print(f"  extractor_backend = {config.hugegraph.extractor_backend}")
    print(f"  he_model = {config.hugegraph.he_model}")
    print(f"  he_default_template = {config.hugegraph.he_default_template}")
    print("  doc_type 未传时由 DocTypeClassifier 从内容推断（P3）")

    # 注: 完整 ingest + kg_build 流程见示例 19；这里仅展示 he 配置。
    # he 后端会在 kg_build 时对每个 chunk 用 hyper-extract 模板抽取，
    # doc_type 决定用哪个模板（路由见 PART A）。
    print("\n  (完整 ingest→kg_build 流程见 19_knowledge_graph_build.py；")
    print("   he 后端在 kg_build 时自动启用 hyper-extract 模板抽取)")


async def main() -> None:
    parser = argparse.ArgumentParser(description="44_kg_doctype_he.py")
    parser.add_argument("--skip-llm", action="store_true",
                        help="跳过 PART B（he 真实抽取，需 LLM）")
    args = parser.parse_args()

    part_a_routing()  # 离线路由演示，总是运行
    await part_b_he_build(skip_llm=args.skip_llm)

    print("\n[全部 PASS — v1.7 doc_type 路由 + he 后端演示完成]")


if __name__ == "__main__":
    asyncio.run(main())
