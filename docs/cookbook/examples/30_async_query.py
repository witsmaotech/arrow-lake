#!/usr/bin/env python3
"""30 — 异步查询

场景: 使用 asyncio 并行执行多个查询，对比串行与并行性能。

数据文件: datas/kb/knowledge_zh.jsonl, datas/papers/metadata_zh.csv
"""

from __future__ import annotations

import argparse

import asyncio
import shutil
import sys
import time
from pathlib import Path

from arrow_lake import Lake

DATAS_DIR = Path(__file__).resolve().parent.parent / "datas"
_DEFAULT_BASE_URI = "./_tmp_async"

# 本脚本创建的所有数据集
_DATASETS = ["kb_zh", "papers_zh"]


async def _run_query(lake: Lake, dataset: str, sql: str, label: str) -> dict:
    """单个查询任务"""
    start = time.perf_counter()
    result = lake.olap_query(dataset, sql)
    elapsed = time.perf_counter() - start
    rows = result.row_count
    return {"label": label, "rows": rows, "elapsed": elapsed}


async def run_async() -> None:
    parser = argparse.ArgumentParser(description="30_async_query.py")
    parser.add_argument("--base-uri", default=_DEFAULT_BASE_URI)
    parser.add_argument("--no-cleanup", action="store_true")
    args = parser.parse_args()
    no_cleanup = args.no_cleanup
    print("=" * 60)
    print("30 异步查询")
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

    # STEP 1: 摄入两个数据集
    print("STEP 1: 摄入数据")
    r1 = lake.ingest("kb_zh", [str(DATAS_DIR / "kb" / "knowledge_zh.jsonl")])
    r2 = lake.ingest("papers_zh", [str(DATAS_DIR / "papers" / "metadata_zh.csv")])
    print(f"  kb_zh: {r1.total_rows} 行, papers_zh: {r2.total_rows} 行")

    queries = [
        ("kb_zh", "SELECT category, COUNT(*) as cnt FROM kb_zh GROUP BY category", "KB 分类统计"),
        ("papers_zh", "SELECT category, COUNT(*) as cnt FROM papers_zh GROUP BY category", "论文分类统计"),
        ("kb_zh", "SELECT COUNT(*) as total FROM kb_zh", "KB 总量"),
        ("papers_zh", "SELECT COUNT(*) as total FROM papers_zh", "论文总量"),
    ]

    # STEP 2: 串行查询
    print("\nSTEP 2: 串行查询")
    start = time.perf_counter()
    for dataset, sql, label in queries:
        result = await _run_query(lake, dataset, sql, label)
        print(f"  {result['label']:<12} {result['rows']:>3} 行  {result['elapsed']:.3f}s")
    serial_time = time.perf_counter() - start
    print(f"  串行总耗时: {serial_time:.3f}s")

    # STEP 3: 并行查询
    print("\nSTEP 3: 并行查询 (asyncio.gather)")
    start = time.perf_counter()
    tasks = [_run_query(lake, ds, sql, lbl) for ds, sql, lbl in queries]
    results = await asyncio.gather(*tasks)
    for r in sorted(results, key=lambda x: x["elapsed"]):
        print(f"  {r['label']:<12} {r['rows']:>3} 行  {r['elapsed']:.3f}s")
    parallel_time = time.perf_counter() - start
    print(f"  并行总耗时: {parallel_time:.3f}s")

    # STEP 4: 性能对比
    print("\nSTEP 4: 性能对比")
    speedup = serial_time / parallel_time if parallel_time > 0 else 1
    print(f"  串行: {serial_time:.3f}s")
    print(f"  并行: {parallel_time:.3f}s")
    print(f"  加速比: {speedup:.2f}x")

    # STEP 5: Session Manager 说明
    print("\nSTEP 5: DuckDB Session Manager")
    print("  Lake 内部使用 DuckDBSessionManager 管理:")
    print("    - 信号量控制并发 (max_concurrent_queries)")
    print("    - 连接内存限制")
    print("    - 空闲连接回收")
    print("    - 慢查询记录")

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
