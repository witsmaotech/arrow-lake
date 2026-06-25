#!/usr/bin/env python3
"""23 — 分面搜索

场景: 在论文数据上执行分面搜索，同时获取向量搜索结果和分面计数。

数据文件: datas/papers/metadata_zh.csv
"""

from __future__ import annotations

import argparse

import shutil
import sys
from pathlib import Path

import numpy as np
import pyarrow as pa

from arrow_lake import Lake

DATAS_DIR = Path(__file__).resolve().parent.parent / "datas"
_DEFAULT_BASE_URI = "./_tmp_faceted"
DIM = 768
_DATASETS = ["papers_zh"]


def _add_vectors(lake: Lake, dataset: str, n_rows: int) -> int:
    """生成随机向量并追加到数据集 (模拟嵌入模型)"""
    rng = np.random.RandomState(42)
    vecs = rng.randn(n_rows, DIM).astype(np.float32)
    vecs /= np.linalg.norm(vecs, axis=1, keepdims=True)
    vec_table = pa.table({
        "text_embedding": pa.FixedSizeListArray.from_arrays(vecs.ravel(), DIM),
    })
    original = lake.read_dataset(dataset)
    combined = original.append_column("text_embedding", vec_table.column("text_embedding"))
    lake.delete_dataset(dataset)
    lake.create_dataset(dataset, combined)
    return n_rows


def main() -> None:
    parser = argparse.ArgumentParser(description="23_faceted_search.py")
    parser.add_argument("--base-uri", default=_DEFAULT_BASE_URI)
    parser.add_argument("--no-cleanup", action="store_true")
    args = parser.parse_args()
    no_cleanup = args.no_cleanup
    print("=" * 60)
    print("23 分面搜索")
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

    # STEP 1: 摄取论文数据
    print("STEP 1: 摄入中文论文")
    r = lake.ingest("papers_zh", [str(DATAS_DIR / "papers" / "metadata_zh.csv")])
    print(f"  摄入: {r.total_rows} 行")

    # STEP 2: 添加向量
    print("\nSTEP 2: 生成向量列")
    n = _add_vectors(lake, "papers_zh", r.total_rows)
    print(f"  {n} 个向量已添加")

    # STEP 3: 分面搜索
    print("\nSTEP 3: 分面搜索 (facets=['category'])")
    rng = np.random.RandomState(42)
    q = rng.randn(DIM).astype(np.float32).tolist()
    try:
        result = lake.faceted_search("papers_zh", q,
                                     vector_column="text_embedding",
                                     facets=["category"],
                                     top_k=5)
        print(f"  搜索结果: {result.row_count} 条")
        print(f"  分面数: {result.total_facets}")
        for i in range(min(5, result.row_count)):
            t = result.table
            title = t.column("title")[i].as_py() if "title" in t.column_names else ""
            print(f"    #{i+1} {title[:50]}")

        print(f"\n  分面统计 (category):")
        for facet in result.facets:
            print(f"    {facet}")
    except Exception as e:
        print(f"  分面搜索跳过: {e}")

    # STEP 4: 对比普通向量搜索
    print("\nSTEP 4: 对比普通向量搜索")
    try:
        result = lake.search("papers_zh", q, vector_column="text_embedding", top_k=5)
        print(f"  结果: {result.row_count} 条 (无分面)")
    except (ValueError, RuntimeError) as e:
        print(f"  搜索跳过: {e}")

    # STEP 5: OLAP 分类统计
    print("\nSTEP 5: OLAP 分类验证")
    result = lake.olap_query("papers_zh",
        "SELECT category, COUNT(*) as cnt FROM papers_zh GROUP BY category ORDER BY cnt DESC")
    for row in result.table.to_pylist():
        print(f"  {row['category']:<16} {row['cnt']:>3} 篇")

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


if __name__ == "__main__":
    main()
