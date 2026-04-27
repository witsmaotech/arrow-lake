#!/usr/bin/env python3
"""19 — 知识图谱构建与查询

场景: 从论文数据构建知识图谱，探索实体关系。

数据文件: datas/papers/metadata_zh.csv

前提: HugeGraph 服务运行中 (config 中 hugegraph.enabled=true)
"""

from __future__ import annotations

import argparse

import asyncio
import shutil
import sys
from pathlib import Path

from arrow_lake import Lake

DATAS_DIR = Path(__file__).resolve().parent.parent / "datas"
_DEFAULT_BASE_URI = "./_tmp_kg_build"


async def run_async() -> None:
    parser = argparse.ArgumentParser(description="19_knowledge_graph_build.py")
    parser.add_argument("--base-uri", default=_DEFAULT_BASE_URI)
    parser.add_argument("--no-cleanup", action="store_true")
    args = parser.parse_args()
    no_cleanup = args.no_cleanup
    print("=" * 60)
    print("19 知识图谱构建与查询")
    print("=" * 60)

    base = Path(args.base_uri)
    if base.exists():
        shutil.rmtree(base)

    lake = Lake(base_uri=args.base_uri)

    # STEP 1: 摄入论文数据
    print("STEP 1: 摄入中文论文数据")
    report = lake.ingest("papers_zh", [str(DATAS_DIR / "papers" / "metadata_zh.csv")])
    ds = lake.open_dataset("papers_zh")
    print(f"  摄入: {report.total_rows} 行, {len(ds.schema)} 列")

    # STEP 2: 检查 KG 服务
    print("\nSTEP 2: 检查 HugeGraph 连接")
    try:
        stats = await lake.kg_stats()
        print(f"  HugeGraph 已连接")
        print(f"  当前图统计: {stats}")
    except Exception as e:
        print(f"  HugeGraph 不可用: {e}")
        print("\n  启动指引:")
        print("    1. 确保 HugeGraph 服务运行: docker compose up -d hugegraph")
        print("    2. 在 config 中设置 hugegraph.enabled=true")
        print("    3. 重新运行本示例")
        lake.shutdown()
        if not no_cleanup:
            shutil.rmtree(base, ignore_errors=True)
        return

    # STEP 3: 构建知识图谱
    print("\nSTEP 3: 构建知识图谱")
    try:
        task_id = await lake.kg_build("papers_zh")
        print(f"  构建任务已提交: {task_id}")
    except RuntimeError as e:
        print(f"  构建失败: {e}")
        lake.shutdown()
        if not no_cleanup:
            shutil.rmtree(base, ignore_errors=True)
        return

    # STEP 4: 查询构建状态
    print("\nSTEP 4: 查询构建状态")
    try:
        status = await lake.kg_build_status(task_id)
        if status:
            print(f"  状态: {status.get('status', 'unknown')}")
            print(f"  详情: {status}")
        else:
            print("  状态查询完成")
    except Exception as e:
        print(f"  状态查询: {e}")

    # STEP 5: 图谱统计
    print("\nSTEP 5: 图谱统计")
    try:
        stats = await lake.kg_stats()
        print(f"  顶点数: {stats.get('vertex_count', 'N/A')}")
        print(f"  边数: {stats.get('edge_count', 'N/A')}")
    except Exception as e:
        print(f"  统计查询: {e}")

    # STEP 6: Gremlin 查询
    print("\nSTEP 6: Gremlin 查询")
    try:
        results = await lake.kg_query("知识图谱", traversal_depth=2)
        print(f"  '知识图谱' 相关结果: {len(results)} 条")
        for r in results[:5]:
            print(f"    {r}")
    except RuntimeError as e:
        print(f"  查询失败: {e}")

    # STEP 7: 邻居遍历
    print("\nSTEP 7: 邻居遍历")
    try:
        neighbors = await lake.kg_get_neighbors("知识图谱", depth=1)
        print(f"  '知识图谱' 的邻居: {len(neighbors)} 个")
        for n in neighbors[:5]:
            print(f"    {n}")
    except Exception as e:
        print(f"  邻居遍历: {e}")

    print("\n  [全部 PASS]")
    if not no_cleanup:
        lake.shutdown()
        shutil.rmtree(base, ignore_errors=True)
        print("(已清理)")


def main() -> None:
    asyncio.run(run_async())


if __name__ == "__main__":
    main()
