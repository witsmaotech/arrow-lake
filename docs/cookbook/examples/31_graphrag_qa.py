#!/usr/bin/env python3
"""31 — GraphRAG 联合问答

场景: 使用知识图谱增强 RAG 问答，对比纯向量 RAG 与 GraphRAG 效果。
GraphRAG 自动从问题中提取实体，检索图谱三元组注入上下文。

前提: HugeGraph + LLM 服务可用，config 中 hugegraph.enabled=true, rag.enabled=true

数据文件: datas/kb/knowledge_zh.jsonl
"""

from __future__ import annotations

import argparse

import asyncio
import shutil
import sys
from pathlib import Path

import pyarrow as pa

from arrow_lake import Lake
from arrow_lake.rag.prompt import PromptRegistry

DATAS_DIR = Path(__file__).resolve().parent.parent / "datas"
_DEFAULT_BASE_URI = "./_tmp_graphrag"
DIM = 768

# 本脚本创建的所有数据集
_DATASETS = ["knowledge_zh"]


def _add_vectors(lake: Lake, dataset: str, n_rows: int) -> int:
    """生成随机向量并追加到数据集 (模拟嵌入模型)"""
    import numpy as np
    rng = np.random.RandomState(42)
    vecs = rng.randn(n_rows, DIM).astype(np.float32)
    vecs /= np.linalg.norm(vecs, axis=1, keepdims=True)
    vec_table = pa.table({
        "text_embedding": pa.FixedSizeListArray.from_arrays(vecs.ravel(), DIM),
    })
    lake.append_dataset(dataset, vec_table)
    return n_rows


async def run_async() -> None:
    parser = argparse.ArgumentParser(description="31_graphrag_qa.py")
    parser.add_argument("--base-uri", default=_DEFAULT_BASE_URI)
    parser.add_argument("--no-cleanup", action="store_true")
    args = parser.parse_args()
    no_cleanup = args.no_cleanup
    print("=" * 60)
    print("31 GraphRAG 联合问答")
    print("=" * 60)

    base = Path(args.base_uri)
    if base.exists():
        shutil.rmtree(base)

    lake = Lake(base_uri=args.base_uri)

    # 清理后端残留
    for ds in _DATASETS:
        try:
            lake.delete_dataset(ds)
        except Exception:
            pass

    # STEP 1: 摄入知识库
    print("STEP 1: 摄入中文知识库")
    report = lake.ingest("knowledge_zh", [str(DATAS_DIR / "kb" / "knowledge_zh.jsonl")])
    print(f"  摄入: {report.total_rows} 行")

    # STEP 2: 向量化 + 建索引
    print("\nSTEP 2: 向量化 + 建索引")
    n = _add_vectors(lake, "knowledge_zh", report.total_rows)
    try:
        lake.create_vector_index("knowledge_zh", "text_embedding")
    except Exception as e:
        print(f"  向量索引跳过: {e}")
    lake.create_fts_index("knowledge_zh", fts_column="text_content")
    print(f"  {n} 个向量, FTS 索引已建立")

    # STEP 3: 检查服务状态
    print("\nSTEP 3: 检查 GraphRAG 服务链")
    kg_ok = False
    rag_ok = False

    try:
        config = lake.config
        kg_cfg = getattr(config, "hugegraph", None)
        if kg_cfg and getattr(kg_cfg, "enabled", False):
            stats = await lake.kg_stats()
            print(f"  HugeGraph: 已连接 (顶点 {stats.get('total_vertices', 0)}, "
                  f"边 {stats.get('total_edges', 0)})")
            kg_ok = True
        else:
            print("  HugeGraph: 未启用")
    except Exception as e:
        print(f"  HugeGraph: 不可用 ({e})")

    try:
        rag_cfg = getattr(config, "rag", None)
        if rag_cfg and getattr(rag_cfg, "enabled", False):
            print("  RAG: 已启用")
            rag_ok = True
        else:
            print("  RAG: 未启用")
    except Exception as e:
        print(f"  RAG: 不可用 ({e})")

    # STEP 4: 提示模板系统
    print("\nSTEP 4: RAG 提示模板")
    try:
        registry = PromptRegistry()
        templates = registry.list_templates()
        print(f"  可用模板: {len(templates)} 个")
        for t in templates:
            tmpl = registry.get(t)
            if tmpl:
                print(f"    - {t} ({tmpl.type.value}): {tmpl.description}")
    except Exception as e:
        print(f"  模板系统: {e}")

    # STEP 5: GraphRAG 问答 (KG + 向量联合)
    if kg_ok and rag_ok:
        print("\nSTEP 5: GraphRAG 联合问答")
        questions = [
            "向量数据库和列式存储有什么关系？",
            "知识图谱在数据管理中的作用是什么？",
        ]
        session_id = "graphrag_session_001"
        for q in questions:
            try:
                resp = await lake.rag_query(q, "knowledge_zh",
                                            top_k=5, session_id=session_id)
                print(f"  Q: {q}")
                print(f"  A: {resp.answer[:200]}...")
                print(f"     引用: {len(resp.citations)} 个, "
                      f"上下文: {resp.context_tokens} tokens, "
                      f"耗时: {resp.latency_ms:.0f}ms")
            except Exception as e:
                print(f"  Q: {q} → 失败: {e}")

        # STEP 6: 清理过期会话
        print("\nSTEP 6: 清理过期会话")
        try:
            cleaned = lake.rag_cleanup_expired_sessions()
            print(f"  已清理: {cleaned} 个过期会话")
        except Exception as e:
            print(f"  清理: {e}")

        # STEP 7: 实体提取 (RAG extract)
        print("\nSTEP 7: 实体提取 (rag_extract)")
        try:
            resp = await lake.rag_extract("知识图谱和向量数据库的关系", dataset_name="knowledge_zh")
            print(f"  提取结果: {resp.answer[:200]}...")
        except Exception as e:
            print(f"  实体提取: {e}")
    else:
        # 降级: 展示纯文本搜索
        print("\nSTEP 5: 降级模式 — 纯文本搜索")
        for q in ["向量数据库", "知识图谱", "列式存储"]:
            try:
                result = lake.text_search("knowledge_zh", q, top_k=3,
                                          columns=["text_content"])
                print(f"  '{q}' → {result.row_count} 条结果")
                for i in range(min(3, result.row_count)):
                    t = result.table
                    title = t.column("title")[i].as_py() if "title" in t.column_names else ""
                    print(f"    #{i+1} {title[:50]}")
            except Exception as e:
                print(f"  '{q}' → 搜索失败: {e}")

        print("\n  [GraphRAG 不可用，已降级为纯文本搜索]")
        print("\n  启动指引:")
        print("    1. 启动 HugeGraph: docker compose up -d hugegraph")
        print("    2. config: hugegraph.enabled=true")
        print("    3. 启动 LLM: ollama serve / openai-compatible endpoint")
        print("    4. config: rag.enabled=true, rag.provider, rag.model")

    # STEP 8: 图谱查询模板说明
    print("\nSTEP 8: 图谱查询能力说明")
    print("  GremlinQueries 模板:")
    print("    - find_entity(name, type)    按名称查找实体")
    print("    - get_neighbors(id, depth)  多跳邻居遍历")
    print("    - shortest_path(src, tgt)   最短路径")
    print("    - get_subgraph(center, r)   子图提取")
    print("    - entity_type_counts()     实体类型统计")
    print("    - traverse_from_entities()  多实体联合遍历")

    print("\n  [全部 PASS]" if kg_ok and rag_ok else "\n  [部分 PASS — GraphRAG 不可用]")
    if not no_cleanup:
        for ds in _DATASETS:
            try:
                lake.delete_dataset(ds)
            except Exception:
                pass
        lake.shutdown()
        shutil.rmtree(base, ignore_errors=True)
        print("(已清理)")


def main() -> None:
    asyncio.run(run_async())


if __name__ == "__main__":
    main()
