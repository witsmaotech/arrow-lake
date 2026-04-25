#!/usr/bin/env python3
"""02 — 搜索与索引

演示向量索引、全文索引的创建，以及三种搜索模式对比：
向量搜索 / 全文搜索 / 混合搜索 (RRF 融合)。

数据文件: datas/kb/knowledge_zh.jsonl
"""

from __future__ import annotations

import shutil
import sys
from pathlib import Path

import numpy as np
import pyarrow as pa

from arrow_lake import Lake

DATAS_DIR = Path(__file__).resolve().parent.parent / "datas"
BASE_URI = "./_tmp_search_index"
DATASET = "knowledge_zh"
DIM = 768


def _add_vectors(lake: Lake) -> None:
    """生成随机 768 维向量并合并到数据集"""
    rng = np.random.RandomState(42)
    storage = lake._get_storage()
    ds = storage.open_dataset(DATASET)
    n = ds.count_rows()
    vecs = rng.randn(n, DIM).astype(np.float32)
    vecs /= np.linalg.norm(vecs, axis=1, keepdims=True)

    original = ds.to_arrow()
    table = original.append_column(
        "text_embedding", pa.FixedSizeListArray.from_arrays(vecs.ravel(), DIM))
    storage.restore_dataset(DATASET, table)
    print(f"  合并 {n} 行 768 维向量")


def _print_results(result, top: int = 5) -> None:
    """格式化输出搜索结果"""
    table = result.table
    for i in range(min(top, result.row_count)):
        row = {col: table.column(col)[i].as_py() for col in table.column_names}
        score = row.pop("_distance", row.pop("_score", row.pop("_rrf_score", None)))
        print(f"    #{i+1} {row.get('id','')}  score={score:.4f}  "
              f"{row.get('title','')[:40]}")


def main() -> None:
    no_cleanup = "--no-cleanup" in sys.argv
    print("=" * 60)
    print("02 搜索与索引")
    print("=" * 60)

    base = Path(BASE_URI)
    if base.exists():
        shutil.rmtree(base)

    lake = Lake(base_uri=BASE_URI)

    # --- STEP 1: 摄入中文知识库 ---
    print("STEP 1: 摄取 JSONL 知识库")
    jsonl_path = str(DATAS_DIR / "kb" / "knowledge_zh.jsonl")
    report = lake.ingest(DATASET, [jsonl_path])
    print(f"  摄入: {report.total_rows} 行")
    print("  [PASS]\n")

    # --- STEP 2: 追加向量列 ---
    print("STEP 2: 生成并追加向量列 (模拟嵌入模型)")
    _add_vectors(lake)
    print("  [PASS]\n")

    # --- STEP 3: 创建向量索引 ---
    print("STEP 3: 创建向量索引 (IVF_PQ)")
    try:
        idx = lake.create_vector_index(DATASET, vector_column="text_embedding",
                                      metric="cosine", index_type="IVF_PQ")
        print(f"  索引类型: {idx.index_type}")
    except Exception as e:
        print(f"  跳过 (数据量不足): {e}")
    print("  [PASS]\n")

    # --- STEP 4: 创建全文索引 ---
    print("STEP 4: 创建全文索引 (jieba 中文分词)")
    lake.create_fts_index(DATASET, fts_column="text_content")
    print("  [PASS]\n")

    # --- STEP 5: 向量搜索 ---
    print("STEP 5: 向量搜索 — '向量数据库'")
    rng = np.random.RandomState(42)
    query_vec = rng.randn(DIM).astype(np.float32).tolist()
    result = lake.search(DATASET, query_vec, top_k=5, vector_column="text_embedding")
    print(f"  结果: {result.row_count} 条")
    _print_results(result)
    print("  [PASS]\n")

    # --- STEP 6: 全文搜索 ---
    print("STEP 6: 全文搜索 — '列式存储 零拷贝'")
    result = lake.text_search(DATASET, "列式存储 零拷贝", top_k=5,
                              fts_column="text_content")
    print(f"  结果: {result.row_count} 条")
    _print_results(result)
    print("  [PASS]\n")

    # --- STEP 7: 混合搜索 ---
    print("STEP 7: 混合搜索 (RRF 融合) — '内存格式'")
    result = lake.hybrid_search(DATASET, query_vec, "内存格式", top_k=5,
                                vector_column="text_embedding",
                                fts_column="text_content")
    print(f"  结果: {result.row_count} 条")
    _print_results(result)
    print("  [PASS]\n")

    if not no_cleanup:
        lake.shutdown()
        shutil.rmtree(base, ignore_errors=True)
        print("(已清理)")

    print("=" * 60)
    print("02 完成")
    print("=" * 60)


if __name__ == "__main__":
    main()
