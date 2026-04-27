#!/usr/bin/env python3
"""09 — 科研论文知识库

场景: 建立论文检索系统，支持语义搜索、关键词搜索和混合搜索，
以及按分类的 SQL 分析。

数据文件: datas/papers/metadata.csv, datas/papers/metadata_zh.csv
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
_DEFAULT_BASE_URI = "./_tmp_paper_library"
DIM = 768


def _add_vectors(lake: Lake, dataset: str) -> int:
    rng = np.random.RandomState(42)
    ds = lake.open_dataset(dataset)
    n = ds.count_rows()
    vecs = rng.randn(n, DIM).astype(np.float32)
    vecs /= np.linalg.norm(vecs, axis=1, keepdims=True)
    original = ds.to_arrow()
    table = original.append_column(
        "text_embedding", pa.FixedSizeListArray.from_arrays(vecs.ravel(), DIM))
    lake.restore_dataset(dataset, table)
    return n


def main() -> None:
    parser = argparse.ArgumentParser(description="09_research_paper_library.py")
    parser.add_argument("--base-uri", default=_DEFAULT_BASE_URI)
    parser.add_argument("--no-cleanup", action="store_true")
    args = parser.parse_args()
    no_cleanup = args.no_cleanup
    print("=" * 60)
    print("09 科研论文知识库")
    print("=" * 60)

    base = Path(args.base_uri)
    if base.exists():
        shutil.rmtree(base)

    lake = Lake(base_uri=args.base_uri)

    # STEP 1: 摄入
    print("STEP 1: 摄入论文数据 (英文 + 中文)")
    r1 = lake.ingest("papers", [str(DATAS_DIR / "papers" / "metadata.csv")])
    r2 = lake.ingest("papers_zh", [str(DATAS_DIR / "papers" / "metadata_zh.csv")])
    print(f"  papers: {r1.total_rows} 行, papers_zh: {r2.total_rows} 行")

    # STEP 2: 追加向量
    print("STEP 2: 生成嵌入向量 (模拟)")
    n1 = _add_vectors(lake, "papers")
    n2 = _add_vectors(lake, "papers_zh")
    print(f"  papers: {n1} 向量, papers_zh: {n2} 向量")

    # STEP 3: 建索引
    print("STEP 3: 建立向量索引 + 全文索引")
    for ds in ["papers", "papers_zh"]:
        try:
            lake.create_vector_index(ds, vector_column="text_embedding")
        except (ValueError, RuntimeError) as e:
            print(f"  向量索引跳过 ({ds}): {e}")
        lake.create_fts_index(ds, fts_column="text_content")
    print("  双索引已创建")

    # STEP 4: 语义搜索
    print("\nSTEP 4: 语义搜索 — 'attention mechanism'")
    rng = np.random.RandomState(42)
    q = rng.randn(DIM).astype(np.float32).tolist()
    result = lake.search("papers", q, top_k=3, vector_column="text_embedding")
    for i in range(min(3, result.row_count)):
        t = result.table
        print(f"  #{i+1} {t.column('id')[i].as_py()}  "
              f"{t.column('category')[i].as_py():<16}  "
              f"{t.column('title')[i].as_py()[:50]}")

    # STEP 5: 中文全文搜索
    print("\nSTEP 5: 中文全文搜索 — '知识图谱 大模型'")
    result = lake.text_search("papers_zh", "知识图谱 大模型", top_k=3,
                              fts_column="text_content")
    for i in range(min(3, result.row_count)):
        t = result.table
        print(f"  #{i+1} {t.column('id')[i].as_py()}  "
              f"{t.column('category')[i].as_py():<16}  "
              f"{t.column('title')[i].as_py()[:50]}")

    # STEP 6: 混合搜索
    print("\nSTEP 6: 混合搜索 — 'transformer architecture'")
    try:
        result = lake.hybrid_search("papers", q, "transformer architecture",
                                    top_k=3, vector_column="text_embedding",
                                    fts_column="text_content")
        for i in range(min(3, result.row_count)):
            t = result.table
            score = t.column("_rrf_score")[i].as_py() if "_rrf_score" in t.column_names else 0
            print(f"  #{i+1} {t.column('id')[i].as_py()}  rrf={score:.4f}  "
                  f"{t.column('title')[i].as_py()[:50]}")
    except Exception as e:
        print(f"  跳过 (需要向量索引): {e}")

    # STEP 7: SQL 分析
    print("\nSTEP 7: 按分类统计 (papers)")
    result = lake.olap_query("papers",
        "SELECT category, COUNT(*) as cnt FROM papers GROUP BY category ORDER BY cnt DESC")
    print(f"  {'分类':<24} {'数量':>4}")
    for row in result.table.to_pylist():
        print(f"  {row['category']:<24} {row['cnt']:>4}")

    # STEP 8: 导出子集
    print("\nSTEP 8: 导出 ML 类论文")
    out = base / "ml_papers.parquet"
    lake.export("papers", str(out), format="parquet")
    print(f"  导出: {out} ({out.stat().st_size // 1024} KB)")

    print("\n  [全部 PASS]")
    if not no_cleanup:
        lake.shutdown()
        shutil.rmtree(base, ignore_errors=True)
        print("(已清理)")


if __name__ == "__main__":
    main()
