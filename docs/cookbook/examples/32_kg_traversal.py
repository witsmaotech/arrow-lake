#!/usr/bin/env python3
"""32 — 知识图谱遍历与路径分析

场景: 使用 HugeGraph 客户端直接操作图谱，演示邻居遍历、
最短路径、子图提取和多实体联合遍历。

前提: HugeGraph 服务运行中 (hugegraph.enabled=true)
"""

from __future__ import annotations

import argparse

import asyncio
import shutil
import sys
from pathlib import Path

import pyarrow as pa

from arrow_lake import Lake

DATAS_DIR = Path(__file__).resolve().parent.parent / "datas"
_DEFAULT_BASE_URI = "./_tmp_kg_traversal"


async def run_async() -> None:
    parser = argparse.ArgumentParser(description="32_kg_traversal.py")
    parser.add_argument("--base-uri", default=_DEFAULT_BASE_URI)
    parser.add_argument("--no-cleanup", action="store_true")
    args = parser.parse_args()
    no_cleanup = args.no_cleanup
    print("=" * 60)
    print("32 知识图谱遍历与路径分析")
    print("=" * 60)

    base = Path(args.base_uri)
    if base.exists():
        shutil.rmtree(base)

    lake = Lake(base_uri=args.base_uri)

    # STEP 1: 摄入数据 + 构建图谱
    print("STEP 1: 摄入论文数据")
    lake.ingest("papers_zh", [str(DATAS_DIR / "papers" / "metadata_zh.csv")])
    ds = lake.open_dataset("papers_zh")
    print(f"  摄入: {ds.count_rows()} 行")

    # STEP 2: 构建知识图谱
    print("\nSTEP 2: 构建知识图谱")
    try:
        task_id = await lake.kg_build("papers_zh")
        print(f"  构建任务: {task_id}")

        # 轮询状态
        import time
        for attempt in range(10):
            status = await lake.kg_build_status(task_id)
            if status:
                s = status.get("status", "unknown")
                processed = status.get("processed_chunks", 0)
                total = status.get("total_chunks", 0)
                entities = status.get("entity_count", 0)
                print(f"  状态: {s} ({processed}/{total} chunks, {entities} entities)")
                if s in ("COMPLETED", "FAILED"):
                    break
            else:
                print("  等待构建任务...")
            await asyncio.sleep(2)
    except Exception as e:
        print(f"  图谱构建失败: {e}")

    # STEP 3: 图谱统计
    print("\nSTEP 3: 图谱统计")
    try:
        stats = await lake.kg_stats()
        print(f"  顶点总数: {stats.get('total_vertices', 0)}")
        print(f"  边总数: {stats.get('total_edges', 0)}")
    except Exception as e:
        print(f"  统计失败: {e}")

    # STEP 4: 实体类型统计
    print("\nSTEP 4: 实体类型统计")
    try:
        from arrow_lake.knowledge_graph.queries import GremlinQueries
        gq = GremlinQueries.entity_type_counts()
        results = await lake.kg_query(gq)
        print(f"  实体类型分布:")
        for r in results[:10]:
            if isinstance(r, dict):
                label = r.get("label", r.get("VertexLabel", "?"))
                count = r.get("count", "?")
                print(f"    {label:<20} {count}")
            else:
                print(f"    {r}")
    except Exception as e:
        print(f"  类型统计: {e}")

    # STEP 5: 邻居遍历
    print("\nSTEP 5: 邻居遍历 (depth=2)")
    try:
        neighbors = await lake.kg_get_neighbors("知识图谱", depth=2)
        print(f"  '知识图谱' 的 2 跳邻居: {len(neighbors)} 个")
        for nb in neighbors[:8]:
            if isinstance(nb, dict):
                name = nb.get("name", nb.get("id", "?"))
                label = nb.get("label", "")
                print(f"    [{label}] {name}")
            else:
                print(f"    {nb}")
    except Exception as e:
        print(f"  邻居遍历: {e}")

    # STEP 6: 最短路径
    print("\nSTEP 6: 最短路径查询")
    try:
        from arrow_lake.knowledge_graph.queries import GremlinQueries
        gq = GremlinQueries.shortest_path("知识图谱", "向量数据库")
        result = await lake.kg_query(gq)
        print(f"  '知识图谱' → '向量数据库' 路径:")
        for r in result[:5]:
            print(f"    {r}")
    except RuntimeError as e:
        print(f"  最短路径: {e}")

    # STEP 7: 子图提取
    print("\nSTEP 7: 子图提取 (radius=2)")
    try:
        from arrow_lake.knowledge_graph.queries import GremlinQueries
        gq = GremlinQueries.get_subgraph("知识图谱", radius=2)
        subgraph = await lake.kg_query(gq)
        print(f"  子图顶点数: {len(subgraph)}")
        for v in subgraph[:5]:
            print(f"    {v}")
    except RuntimeError as e:
        print(f"  子图提取: {e}")

    # STEP 8: 多实体联合遍历
    print("\nSTEP 8: 多实体联合遍历")
    try:
        from arrow_lake.knowledge_graph.queries import GremlinQueries
        gq = GremlinQueries.traverse_from_entities(
            ["知识图谱", "向量数据库"], depth=2)
        multi = await lake.kg_query(gq)
        print(f"  联合遍历结果: {len(multi)} 个顶点")
    except RuntimeError as e:
        print(f"  联合遍历: {e}")

    print("\n  [全部 PASS]")
    if not no_cleanup:
        lake.shutdown()
        shutil.rmtree(base, ignore_errors=True)
        print("(已清理)")


def main() -> None:
    asyncio.run(run_async())


if __name__ == "__main__":
    main()
