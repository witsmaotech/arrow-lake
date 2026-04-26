#!/usr/bin/env python3
"""06 — 论文管理全流程

端到端演示: ingest → 索引 → 搜索 → SQL 分析 → 导出。

数据文件: datas/papers/metadata.csv, datas/papers/metadata_zh.csv
"""

from __future__ import annotations

import shutil
import sys
from pathlib import Path

import numpy as np
import pyarrow as pa

from arrow_lake import Lake

DATAS_DIR = Path(__file__).resolve().parent.parent / "datas"
BASE_URI = "./_tmp_papers_pipeline"
DIM = 768


def _add_vectors(lake: Lake, dataset: str) -> int:
    rng = np.random.RandomState(42)
    storage = lake._get_storage()
    ds = lake.open_dataset(dataset)
    n = ds.count_rows()
    vecs = rng.randn(n, DIM).astype(np.float32)
    vecs /= np.linalg.norm(vecs, axis=1, keepdims=True)
    original = ds.to_arrow()
    table = original.append_column(
        "text_embedding", pa.FixedSizeListArray.from_arrays(vecs.ravel(), DIM))
    storage.restore_dataset(dataset, table)
    return n


def main() -> None:
    no_cleanup = "--no-cleanup" in sys.argv
    print("=" * 60)
    print("06 论文管理全流程")
    print("=" * 60)

    base = Path(BASE_URI)
    if base.exists():
        shutil.rmtree(base)

    lake = Lake(base_uri=BASE_URI)

    # --- STEP 1: 摄取 ---
    print("STEP 1: 摄入论文数据")
    report = lake.ingest("papers", [str(DATAS_DIR / "papers" / "metadata.csv")])
    print(f"  英文论文: {report.total_rows} 行")
    report = lake.ingest("papers_zh", [str(DATAS_DIR / "papers" / "metadata_zh.csv")])
    print(f"  中文论文: {report.total_rows} 行")
    print("  [PASS]\n")

    # --- STEP 2: 追加向量 ---
    print("STEP 2: 追加向量列")
    n1 = _add_vectors(lake, "papers")
    n2 = _add_vectors(lake, "papers_zh")
    print(f"  papers: {n1} 向量, papers_zh: {n2} 向量")
    print("  [PASS]\n")

    # --- STEP 3: 创建索引 ---
    print("STEP 3: 创建索引")
    for ds in ["papers", "papers_zh"]:
        try:
            lake.create_vector_index(ds, vector_column="text_embedding")
        except Exception as e:
            print(f"  向量索引跳过 ({ds}): {e}")
        lake.create_fts_index(ds, fts_column="text_content")
    print("  向量索引 + 全文索引 已创建")
    print("  [PASS]\n")

    # --- STEP 4: 全文搜索 ---
    print("STEP 4: 中文全文搜索 — '知识图谱'")
    result = lake.text_search("papers_zh", "知识图谱", top_k=3, fts_column="text_content")
    print(f"  结果: {result.row_count} 条")
    for i in range(min(3, result.row_count)):
        tbl = result.table
        print(f"    #{i+1} {tbl.column('id')[i].as_py()}  "
              f"{tbl.column('title')[i].as_py()[:50]}")
    print("  [PASS]\n")

    # --- STEP 5: SQL 分析 ---
    print("STEP 5: 按分类统计 (papers_zh)")
    result = lake.olap_query("papers_zh",
        "SELECT category, COUNT(*) as cnt, MIN(word_count) as min_wc, "
        "MAX(word_count) as max_wc FROM papers_zh GROUP BY category ORDER BY cnt DESC")
    print(f"  {'分类':<16} {'数量':>4} {'最少字数':>8} {'最多字数':>8}")
    for row in result.table.to_pylist():
        print(f"  {row['category']:<16} {row['cnt']:>4} {row['min_wc']:>8} {row['max_wc']:>8}")
    print("  [PASS]\n")

    # --- STEP 6: 导出 ---
    print("STEP 6: 导出中文论文子集")
    out = base / "papers_zh_export.parquet"
    lake.export("papers_zh", str(out), format="parquet")
    print(f"  导出: {out} ({out.stat().st_size // 1024} KB)")
    print("  [PASS]\n")

    if not no_cleanup:
        lake.shutdown()
        shutil.rmtree(base, ignore_errors=True)
        print("(已清理)")

    print("=" * 60)
    print("06 完成")
    print("=" * 60)


if __name__ == "__main__":
    main()
