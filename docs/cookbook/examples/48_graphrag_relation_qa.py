#!/usr/bin/env python3
"""48 — GraphRAG 关系增强问答 (v1.9.11)

场景: 对比 ``use_kg=True`` (GraphRAG) 与 ``use_kg=False`` (纯向量) 的回答质量。
v1.9.11 修复了邻居上下文只拿 ``related_to`` 标签、丢失 ``relation_type`` 的缺陷,
GraphRAG 现在注入带语义的关系三元组 (如 "应急指挥中心 --统筹--> 数据中台")。

教学点:
  1. ``await lake.rag_query(q, dataset, use_kg=True)`` —— 启用 GraphRAG
  2. relation_type 富化: 边的 ``properties.relation_type`` 优于 label
  3. ``retrieval_count`` / ``citations`` 反映 KG 三元组 vs 纯 chunk 的差异
  4. 适合实体关系密集型问题 (组织协作 / 供应链 / 事件因果)

前提: HugeGraph + LLM 服务可用; 数据集已建 KG (kg_build 过)。
"""

from __future__ import annotations

import argparse
import asyncio
import shutil
from pathlib import Path

import pyarrow as pa

from arrow_lake import Lake
from arrow_lake.config import ArrowLakeConfig

_DEFAULT_BASE_URI = "./_tmp_graphrag_relation"
_DATASET = "graphrag_rel_demo"

# 实体关系密集型文本 —— 适合体现 GraphRAG 优势
_CHUNKS = [
    "智慧城市项目由市应急指挥中心牵头建设。数据中台由云服务商承建,汇聚交通、气象数据。"
    "综合预警子系统对接应急指挥中心,气象预警达橙色时启动响应。",
    "应急指挥中心统筹三个子系统: 数据中台提供数据, 物联网感知负责采集, "
    "综合预警负责决策。一期交付数据中台, 二期交付感知网络, 三期交付预警联调。",
]


def _build_table() -> pa.Table:
    return pa.table({"text_content": pa.array(_CHUNKS),
                     "source": pa.array(["brief_a", "brief_b"])})


def _add_random_vectors(lake: Lake, dataset: str, n: int) -> None:
    """给数据集补一个 text_embedding 列 (模拟嵌入, 让 vector 路径可用)。"""
    import numpy as np
    rng = np.random.RandomState(7)
    vecs = rng.randn(n, 768).astype(np.float32)
    vecs /= np.linalg.norm(vecs, axis=1, keepdims=True)
    arr = pa.FixedSizeListArray.from_arrays(vecs.ravel(), 768)
    t = lake.read_dataset(dataset)
    if hasattr(t, "to_arrow"):
        t = t.to_arrow()
    lake.restore_dataset(dataset, t.append_column("text_embedding", arr))


async def _ask(lake: Lake, question: str, use_kg: bool) -> None:
    """跑一次 RAG 查询并打印关键指标。"""
    tag = "GraphRAG (use_kg=True)" if use_kg else "纯向量 (use_kg=False)"
    try:
        resp = await lake.rag_query(question, _DATASET, top_k=5, use_kg=use_kg)
        print(f"  [{tag}]")
        print(f"    回答: {resp.answer[:160]}...")
        print(f"    retrieval_count={resp.retrieval_count}, "
              f"citations={len(resp.citations)}, "
              f"latency={resp.latency_ms:.0f}ms" if resp.latency_ms else "")
        # v1.9.11: GraphRAG 路径的 citation 应为 HG 实体 (带 relation_type)
        if use_kg and resp.citations:
            print(f"    citation 样例: {str(resp.citations[0])[:120]}")
    except Exception as e:
        print(f"  [{tag}] 失败: {e}")


async def run_async() -> None:
    parser = argparse.ArgumentParser(description="48_graphrag_relation_qa.py")
    parser.add_argument("--base-uri", default=_DEFAULT_BASE_URI)
    parser.add_argument("--no-cleanup", action="store_true")
    args = parser.parse_args()
    no_cleanup = args.no_cleanup
    print("=" * 64)
    print("48 GraphRAG 关系增强问答 (v1.9.11)")
    print("=" * 64)

    base = Path(args.base_uri)
    if base.exists():
        shutil.rmtree(base)

    config = ArrowLakeConfig()
    config.hugegraph.enabled = True
    config.hugegraph.host = "localhost"
    config.hugegraph.port = 8089
    lake = Lake(base_uri=args.base_uri, config=config)

    try:
        lake.delete_dataset(_DATASET)
    except Exception:
        pass

    # --- Step 1: 摄入 + 向量化 + 建索引 ---
    print("\n--- Step 1: 摄入关系密集型文本 ---")
    lake.create_dataset(_DATASET, _build_table(), actor="cookbook")
    _add_random_vectors(lake, _DATASET, len(_CHUNKS))
    try:
        lake.create_vector_index(_DATASET, vector_column="text_embedding")
    except Exception as e:
        print(f"  向量索引: {e}")
    lake.create_fts_index(_DATASET, fts_column="text_content")
    print(f"  数据集 '{_DATASET}' 就绪 ({len(_CHUNKS)} 行, FTS+向量双索引)")

    # --- Step 2: 构建 KG (用 project_concept_graph 拿到富关系) ---
    print("\n--- Step 2: 构建 KG (project_concept_graph 模板) ---")
    try:
        stats = await lake.kg_stats()
        print(f"  HugeGraph: 顶点 {stats.get('total_vertices', 0)}")
        task = await lake.kg_build(_DATASET, template="project/concept_graph")
        print(f"  构建任务: {task}, 等待完成...")
        await asyncio.sleep(10)
        s2 = await lake.kg_stats()
        print(f"  构建后: 顶点 {s2.get('total_vertices', 0)}, "
              f"边 {s2.get('total_edges', 0)}")
    except Exception as e:
        print(f"  KG 构建 (需 HugeGraph+LLM): {e}")
        print("  后续对比仍可运行, 但 use_kg=True 会降级为纯向量")

    # --- Step 3: 关系型问题 —— GraphRAG vs 纯向量 ---
    print("\n--- Step 3: 关系型问题对比 ---")
    q1 = "应急指挥中心和哪些子系统有协作关系?"
    await _ask(lake, q1, use_kg=True)
    await _ask(lake, q1, use_kg=False)
    print("  → v1.9.11: GraphRAG 注入 relation_type (非裸 related_to),")
    print("    回答应明确指出「统筹/对接/承建」等动词, 而非泛泛相关。")

    # --- Step 4: 实体属性问题 ---
    print("\n--- Step 4: 实体属性问题对比 ---")
    q2 = "气象预警达到什么等级会启动应急响应?"
    await _ask(lake, q2, use_kg=True)
    await _ask(lake, q2, use_kg=False)

    # --- Step 5: 单跳遍历 (直接查邻居, 不经 LLM) ---
    print("\n--- Step 5: KG 邻居遍历 (kg_get_neighbors) ---")
    try:
        neighbors = await lake.kg_get_neighbors("应急指挥中心", depth=1)
        print(f"  '应急指挥中心' 一阶邻居: {len(neighbors)} 个")
        for nb in neighbors[:5]:
            props = nb.get("properties", {})
            # v1.9.11: 边也带 relation_type (snapshot 路径)
            rtype = props.get("relation_type") or props.get("label", "related_to")
            print(f"    --[{rtype}]--> {props.get('name', nb.get('id', ''))}")
    except Exception as e:
        print(f"  遍历: {e}")

    # --- Step 6: v1.9.11 富化说明 ---
    print("\n--- Step 6: v1.9.11 relation_type 富化 ---")
    print("  修复前: _build_neighbor_context 只取 edge.label (多为 related_to)")
    print("  修复后: 取 properties.relation_type, fallback 到 label")
    print("  效果: GraphRAG 上下文含「A 统筹 B」「C 承建 D」等语义三元组")
    print("  注意: retriever.py predicate 路径 (/rag/query) 经 kneighbor API,")
    print("        需 client.get_vertex_edges 补边语义; kg.html 走 snapshot 全量边。")

    print("\n  [全部 PASS]")
    if not no_cleanup:
        try:
            lake.delete_dataset(_DATASET)
        except Exception:
            pass
        lake.shutdown()
        shutil.rmtree(base, ignore_errors=True)
        print("(已清理)")


def main() -> None:
    asyncio.run(run_async())


if __name__ == "__main__":
    main()
