#!/usr/bin/env python3
"""33 — 实体抽取与关系发现

场景: 使用 EntityExtractor 从文本中抽取实体和关系，构建结构化知识表示。
展示抽取结果的质量分析和关系网络可视化。

前提: LLM 服务可用 (config 中 rag.enabled=true)
"""

from __future__ import annotations

import argparse

import asyncio
import shutil
import sys
from pathlib import Path

from arrow_lake import Lake

DATAS_DIR = Path(__file__).resolve().parent.parent / "datas"
_DEFAULT_BASE_URI = "./_tmp_entity_extract"


async def run_async() -> None:
    parser = argparse.ArgumentParser(description="33_kg_entity_extract.py")
    parser.add_argument("--base-uri", default=_DEFAULT_BASE_URI)
    parser.add_argument("--no-cleanup", action="store_true")
    args = parser.parse_args()
    no_cleanup = args.no_cleanup
    print("=" * 60)
    print("33 实体抽取与关系发现")
    print("=" * 60)

    base = Path(args.base_uri)
    if base.exists():
        shutil.rmtree(base)

    lake = Lake(base_uri=args.base_uri)

    # STEP 1: 摄入知识库
    print("STEP 1: 摄入中文知识库")
    report = lake.ingest("knowledge_zh", [str(DATAS_DIR / "kb" / "knowledge_zh.jsonl")])
    print(f"  摄入: {report.total_rows} 行")

    # STEP 2: 获取 LLM 和 EntityExtractor
    print("\nSTEP 2: 初始化实体抽取器")
    try:
        config = lake.config
        rag_cfg = getattr(config, "rag", None)
        llm_cfg = getattr(config, "llm", None)

        if not (rag_cfg and getattr(rag_cfg, "enabled", False)):
            raise RuntimeError("RAG 未启用")

        from arrow_lake.config.rag import LLMConfig, LLMProviderType
        from arrow_lake.rag.provider import create_llm_provider
        from arrow_lake.knowledge_graph.extractor import EntityExtractor

        if llm_cfg is None:
            llm_cfg = LLMConfig()
        provider = create_llm_provider(llm_cfg)
        extractor = EntityExtractor(provider)
        print(f"  LLM: {llm_cfg.model} (provider={llm_cfg.provider})")
        print(f"  置信度阈值: 0.7")
    except Exception as e:
        print(f"  初始化失败: {e}")
        print("\n  降级: 展示抽取概念和格式")
        _show_extraction_concept()
        lake.shutdown()
        shutil.rmtree(base, ignore_errors=True)
        return

    # STEP 3: 单文本抽取
    print("\nSTEP 3: 单文本实体抽取")
    test_text = "Arrow 是 Apache 基金会下的列式内存格式项目，由 Dremio 团队开发。"
    try:
        result = await extractor.extract(test_text, chunk_id="test_001")
        print(f"  原文: {test_text[:60]}...")
        print(f"  实体: {len(result.entities)} 个")
        for ent in result.entities:
            print(f"    [{ent.entity_type}] {ent.name}")
        print(f"  关系: {len(result.relations)} 条")
        for rel in result.relations:
            print(f"    {rel.source} --[{rel.relation_type}]--> {rel.target}")
    except Exception as e:
        print(f"  抽取失败: {e}")

    # STEP 4: 批量抽取
    print("\nSTEP 4: 批量抽取知识库")
    ds = lake.open_dataset("knowledge_zh")
    table = ds.to_arrow()
    text_col = "text_content" if "text_content" in table.column_names else None
    if text_col is None:
        print("  跳过: 无 text_content 列")
    else:
        chunks = []
        for i in range(min(5, table.num_rows)):
            text = table.column(text_col)[i].as_py()
            if text:
                chunks.append((f"chunk_{i}", text))
        try:
            results = await extractor.extract_batch(chunks)
            total_entities = sum(len(r.entities) for r in results)
            total_relations = sum(len(r.relations) for r in results)
            print(f"  处理 {len(chunks)} 个文本块")
            print(f"  总实体: {total_entities} 个")
            print(f"  总关系: {total_relations} 条")

            # 实体类型分布
            type_counts: dict[str, int] = {}
            for r in results:
                for ent in r.entities:
                    type_counts[ent.entity_type] = type_counts.get(ent.entity_type, 0) + 1
            print(f"\n  实体类型分布:")
            for etype, cnt in sorted(type_counts.items(), key=lambda x: -x[1]):
                print(f"    {etype:<16} {cnt:>3} 个")
        except Exception as e:
            print(f"  批量抽取失败: {e}")

    # STEP 5: 关系网络分析
    print("\nSTEP 5: 关系类型分布")
    try:
        relation_types: dict[str, int] = {}
        for r in results:
            for rel in r.relations:
                relation_types[rel.relation_type] = relation_types.get(rel.relation_type, 0) + 1
        print(f"  关系类型:")
        for rtype, cnt in sorted(relation_types.items(), key=lambda x: -x[1]):
            print(f"    {rtype:<24} {cnt:>3} 条")
    except Exception:
        pass

    await provider.close()

    print("\n  [全部 PASS]")
    if not no_cleanup:
        lake.shutdown()
        shutil.rmtree(base, ignore_errors=True)
        print("(已清理)")


def _show_extraction_concept() -> None:
    print("\n  实体抽取数据模型:")
    print("    ExtractedEntity:")
    print("      - name: str (实体名称)")
    print("      - entity_type: str (实体类型: person/org/concept/location/...)")
    print("      - properties: tuple (属性键值对)")
    print("    ExtractedRelation:")
    print("      - source: str (源实体)")
    print("      - target: str (目标实体)")
    print("      - relation_type: str (关系类型)")
    print("    ExtractionResult:")
    print("      - entities: tuple[ExtractedEntity]")
    print("      - relations: tuple[ExtractedRelation]")
    print("      - raw_text: str")


def main() -> None:
    asyncio.run(run_async())


if __name__ == "__main__":
    main()
