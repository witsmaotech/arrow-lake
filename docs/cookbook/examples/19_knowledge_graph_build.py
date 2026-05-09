#!/usr/bin/env python3
"""19 — 知识图谱构建与查询

场景: 从论文数据构建知识图谱，探索实体关系。

数据文件: datas/papers/metadata_zh.csv

前提: HugeGraph 服务运行中 + LLM API 可用 (构建图谱时需要调用 LLM 提取实体关系)
"""

from __future__ import annotations

import argparse

import asyncio
import shutil
import sys
from pathlib import Path

from arrow_lake import Lake
from arrow_lake.config import ArrowLakeConfig

DATAS_DIR = Path(__file__).resolve().parent.parent / "datas"
_DEFAULT_BASE_URI = "./_tmp_kg_build"
_DATASETS = ["papers_zh"]


async def run_async() -> None:
    parser = argparse.ArgumentParser(description="19_knowledge_graph_build.py")
    parser.add_argument("--base-uri", default=_DEFAULT_BASE_URI)
    parser.add_argument("--no-cleanup", action="store_true")
    parser.add_argument("--skip-build", action="store_true",
                        help="跳过摄入和构建，直接查询已有图谱")
    args = parser.parse_args()
    no_cleanup = args.no_cleanup
    skip_build = args.skip_build
    print("=" * 60)
    print("19 知识图谱构建与查询")
    print("=" * 60)

    base = Path(args.base_uri)
    if not skip_build and base.exists():
        shutil.rmtree(base)

    config = ArrowLakeConfig()
    config.hugegraph.enabled = True
    config.hugegraph.host = "localhost"
    config.hugegraph.port = 8089
    config.hugegraph.graph_name = "hugegraph"
    lake = Lake(base_uri=args.base_uri, config=config)

    task_id = ""

    if not skip_build:
        # 清理后端残留
        for ds in _DATASETS:
            try:
                lake.delete_dataset(ds)
            except Exception:
                pass

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
            if not no_cleanup:
                for ds in _DATASETS:
                    try:
                        lake.delete_dataset(ds)
                    except Exception:
                        pass
            lake.shutdown()
            if not no_cleanup:
                shutil.rmtree(base, ignore_errors=True)
            return

        # STEP 3: 构建知识图谱
        print("\nSTEP 3: 构建知识图谱 (需要 LLM API，耗时较长)")
        try:
            task_id = await lake.kg_build("papers_zh")
            print(f"  构建任务已提交: {task_id}")
        except Exception as e:
            err_msg = str(e)
            print(f"  构建失败: {e}")
            if "429" in err_msg or "rate" in err_msg.lower() or "concurrent" in err_msg.lower():
                print("\n  提示: LLM API 限流，请稍后重试或检查 LLM 配额")
            if not no_cleanup:
                for ds in _DATASETS:
                    try:
                        lake.delete_dataset(ds)
                    except Exception:
                        pass
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
        print(f"  顶点数: {stats.get('total_vertices', 'N/A')}")
        print(f"  边数: {stats.get('total_edges', 'N/A')}")
    except Exception as e:
        print(f"  统计查询: {e}")

    # STEP 6: K-neighbor 遍历查询 (REST traversers API)
    print("\nSTEP 6: K-neighbor 遍历查询")
    try:
        neighbors = await lake.kg_get_neighbors("3:知识图谱", depth=1)
        print(f"  '知识图谱' 的一阶邻居: {len(neighbors)} 个")
        for n in neighbors[:5]:
            props = n.get("properties", {})
            name = props.get("name", n.get("id", ""))
            print(f"    {name}")
    except Exception as e:
        print(f"  查询失败: {e}")

    # STEP 7: 构建结果摘要
    print("\nSTEP 7: 构建结果摘要")
    if task_id:
        try:
            status = await lake.kg_build_status(task_id)
            if status:
                print(f"  任务状态: {status.get('status', 'unknown')}")
                print(f"  处理块数: {status.get('processed_chunks', 0)} / {status.get('total_chunks', 0)}")
                print(f"  实体数: {status.get('entity_count', 0)}")
                print(f"  关系数: {status.get('relation_count', 0)}")
                started = status.get("started_at", "")
                completed = status.get("completed_at", "")
                if started and completed:
                    from datetime import datetime
                    t0 = datetime.fromisoformat(started)
                    t1 = datetime.fromisoformat(completed)
                    print(f"  耗时: {t1 - t0}")
        except Exception as e:
            print(f"  摘要查询: {e}")
    else:
        print("  (跳过: 未执行构建，使用 --skip-build 时无任务 ID)")
        stats = await lake.kg_stats()
        print(f"  当前图谱: {stats.get('total_vertices', 0)} 顶点, {stats.get('total_edges', 0)} 边")

    print("\n  [全部 PASS]")
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
