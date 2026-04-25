#!/usr/bin/env python3
"""14 — 跨域混合搜索

场景: 同时搜索论文和知识库，模拟统一检索门户。

数据文件: datas/papers/metadata_zh.csv, datas/kb/knowledge_zh.jsonl
"""

from __future__ import annotations

import shutil
import sys
from pathlib import Path

import numpy as np
import pyarrow as pa

from arrow_lake import Lake

DATAS_DIR = Path(__file__).resolve().parent.parent / "datas"
BASE_URI = "./_tmp_cross_domain"
DIM = 768


def _add_vectors(lake: Lake, dataset: str) -> int:
    rng = np.random.RandomState(42)
    storage = lake._get_storage()
    ds = storage.open_dataset(dataset)
    n = ds.count_rows()
    vecs = rng.randn(n, DIM).astype(np.float32)
    vecs /= np.linalg.norm(vecs, axis=1, keepdims=True)
    original = ds.to_arrow()
    table = original.append_column(
        "text_embedding", pa.FixedSizeListArray.from_arrays(vecs.ravel(), DIM))
    storage.restore_dataset(dataset, table)
    return n


def _show_domain(result, domain: str, top: int = 5) -> None:
    for i in range(min(top, result.row_count)):
        t = result.table
        title = t.column("title")[i].as_py() if "title" in t.column_names else ""
        cat = t.column("category")[i].as_py() if "category" in t.column_names else ""
        score = 0
        for col in ("_rrf_score", "_score", "_distance"):
            if col in t.column_names:
                val = t.column(col)[i].as_py()
                if val is not None:
                    score = val
                break
        print(f"  [{domain}] #{i+1} {title[:40]}  ({cat})  score={score:.4f}")


def main() -> None:
    no_cleanup = "--no-cleanup" in sys.argv
    print("=" * 60)
    print("14 跨域混合搜索")
    print("=" * 60)

    base = Path(BASE_URI)
    if base.exists():
        shutil.rmtree(base)

    lake = Lake(base_uri=BASE_URI)

    # STEP 1: 摄取两个域
    print("STEP 1: 摄取论文 + 知识库")
    r1 = lake.ingest("papers_zh", [str(DATAS_DIR / "papers" / "metadata_zh.csv")])
    r2 = lake.ingest("knowledge_zh", [str(DATAS_DIR / "kb" / "knowledge_zh.jsonl")])
    print(f"  论文: {r1.total_rows} 行, 知识库: {r2.total_rows} 行")

    # STEP 2: 建索引
    print("\nSTEP 2: 向量化 + 建索引")
    n1 = _add_vectors(lake, "papers_zh")
    n2 = _add_vectors(lake, "knowledge_zh")
    for ds in ["papers_zh", "knowledge_zh"]:
        try:
            lake.create_vector_index(ds, vector_column="text_embedding")
        except Exception as e:
            print(f"  向量索引跳过 ({ds}): {e}")
        lake.create_fts_index(ds, fts_column="text_content")
    print(f"  共 {n1 + n2} 个向量, 双域双索引已建立")

    # STEP 3: 跨域关键词搜索
    print("\nSTEP 3: 跨域关键词搜索 — '向量数据库'")
    r_papers = lake.text_search("papers_zh", "向量数据库", top_k=3, fts_column="text_content")
    r_kb = lake.text_search("knowledge_zh", "向量数据库", top_k=3, fts_column="text_content")
    _show_domain(r_papers, "论文", top=3)
    _show_domain(r_kb, "知识库", top=3)

    # STEP 4: 跨域混合搜索
    print("\nSTEP 4: 跨域混合搜索 — '知识图谱 大模型'")
    rng = np.random.RandomState(42)
    q = rng.randn(DIM).astype(np.float32).tolist()
    r_papers = lake.hybrid_search("papers_zh", q, "知识图谱 大模型",
                                   top_k=3, vector_column="text_embedding",
                                   fts_column="text_content")
    r_kb = lake.hybrid_search("knowledge_zh", q, "知识图谱 大模型",
                               top_k=3, vector_column="text_embedding",
                               fts_column="text_content")
    _show_domain(r_papers, "论文", top=3)
    _show_domain(r_kb, "知识库", top=3)

    # STEP 5: 跨域聚合统计
    print("\nSTEP 5: 跨域分类分布对比")
    for ds_name in ["papers_zh", "knowledge_zh"]:
        result = lake.olap_query(ds_name,
            f"SELECT category, COUNT(*) as cnt FROM {ds_name} GROUP BY category ORDER BY cnt DESC")
        print(f"\n  [{ds_name}]")
        for row in result.table.to_pylist():
            print(f"    {row['category']:<16} {row['cnt']:>3} 条")

    # STEP 6: 跨域联合导出
    print("\nSTEP 6: 跨域导出")
    for ds_name in ["papers_zh", "knowledge_zh"]:
        out = base / f"{ds_name}_export.parquet"
        lake.export(ds_name, str(out), format="parquet")
        ds = lake._get_storage().open_dataset(ds_name)
        print(f"  {ds_name}: {ds.count_rows()} 行 → {out.name} ({out.stat().st_size // 1024} KB)")

    print("\n  [全部 PASS]")
    if not no_cleanup:
        lake.shutdown()
        shutil.rmtree(base, ignore_errors=True)
        print("(已清理)")


if __name__ == "__main__":
    main()
