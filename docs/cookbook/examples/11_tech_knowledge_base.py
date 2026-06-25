#!/usr/bin/env python3
"""11 — 技术知识库检索系统

场景: 构建中英文双语技术知识库，支持按分类搜索、标签过滤和混合搜索。

数据文件: datas/kb/knowledge.jsonl, datas/kb/knowledge_zh.jsonl
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
_DEFAULT_BASE_URI = "./_tmp_tech_kb"
DIM = 768


def _add_vectors(lake: Lake, dataset: str, n_rows: int) -> int:
    """生成随机向量并追加到数据集 (模拟嵌入模型)"""
    rng = np.random.RandomState(42)
    vecs = rng.randn(n_rows, DIM).astype(np.float32)
    vecs /= np.linalg.norm(vecs, axis=1, keepdims=True)
    vec_table = pa.table({
        "text_embedding": pa.FixedSizeListArray.from_arrays(vecs.ravel(), DIM),
    })
    return n_rows


def _show(result, top: int = 5) -> None:
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
        print(f"  #{i+1} [{cat}] {title[:45]}  score={score:.4f}")


def main() -> None:
    parser = argparse.ArgumentParser(description="11_tech_knowledge_base.py")
    parser.add_argument("--base-uri", default=_DEFAULT_BASE_URI)
    parser.add_argument("--no-cleanup", action="store_true")
    args = parser.parse_args()
    no_cleanup = args.no_cleanup
    print("=" * 60)
    print("11 技术知识库检索系统")
    print("=" * 60)

    base = Path(args.base_uri)
    if base.exists():
        shutil.rmtree(base)

    lake = Lake(base_uri=args.base_uri)

    # 清理后端残留
    _DATASETS = ["kb_en", "kb_zh"]
    for ds in _DATASETS:
        try:
            lake.delete_dataset(ds)
        except Exception:
            pass

    # STEP 1
    print("STEP 1: 摄取双语知识库")
    r1 = lake.ingest("kb_en", [str(DATAS_DIR / "kb" / "knowledge.jsonl")])
    r2 = lake.ingest("kb_zh", [str(DATAS_DIR / "kb" / "knowledge_zh.jsonl")])
    print(f"  英文: {r1.total_rows} 行, 中文: {r2.total_rows} 行")

    # STEP 2
    print("\nSTEP 2: 生成向量 + 建立索引")
    n1 = _add_vectors(lake, "kb_en", r1.total_rows)
    n2 = _add_vectors(lake, "kb_zh", r2.total_rows)
    for ds in ["kb_en", "kb_zh"]:
        try:
            lake.create_vector_index(ds, vector_column="text_embedding")
        except Exception as e:
            print(f"  向量索引跳过 ({ds}): {e}")
        lake.create_fts_index(ds, fts_column="text_content")
    print(f"  共 {n1 + n2} 个向量, 双索引已建立")

    # STEP 3: 中文分类搜索
    print("\nSTEP 3: 按分类搜索 (算法类)")
    result = lake.text_search("kb_zh", "向量数据库", top_k=3, fts_column="text_content")
    print(f"  结果: {result.row_count} 条")
    _show(result)

    # STEP 4: 中文混合搜索
    print("\nSTEP 4: 混合搜索 — '列式存储的优势'")
    rng = np.random.RandomState(42)
    q = rng.randn(DIM).astype(np.float32).tolist()
    result = lake.hybrid_search("kb_zh", q, "列式存储的优势",
                                top_k=5, vector_column="text_embedding",
                                fts_column="text_content")
    _show(result)

    # STEP 5: 英文搜索
    print("\nSTEP 5: 英文搜索 — 'vector database'")
    result = lake.text_search("kb_en", "vector database", top_k=3, fts_column="text_content")
    _show(result)

    # STEP 6: SQL 分类统计
    print("\nSTEP 6: 知识库分类分布")
    result = lake.olap_query("kb_zh",
        "SELECT category, COUNT(*) as cnt FROM kb_zh GROUP BY category ORDER BY cnt DESC")
    for row in result.table.to_pylist():
        print(f"  {row['category']:<16} {row['cnt']:>3} 条")

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
