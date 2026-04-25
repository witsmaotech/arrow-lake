#!/usr/bin/env python3
"""24 — 集成搜索

场景: 使用多个嵌入向量列执行加权集成搜索，模拟多语言/多模型场景。

数据: 合成 (含中英文双向量列)
"""

from __future__ import annotations

import shutil
import sys
from pathlib import Path

import numpy as np
import pyarrow as pa

from arrow_lake import Lake

BASE_URI = "./_tmp_ensemble"
DIM = 256


def main() -> None:
    no_cleanup = "--no-cleanup" in sys.argv
    print("=" * 60)
    print("24 集成搜索")
    print("=" * 60)

    base = Path(BASE_URI)
    if base.exists():
        shutil.rmtree(base)

    lake = Lake(base_uri=BASE_URI)

    # STEP 1: 创建含多向量列的数据集
    print("STEP 1: 创建含双向量列的数据集")
    rng = np.random.RandomState(42)
    n = 50
    ids = [f"doc_{i:04d}" for i in range(n)]
    texts = [f"文档内容 第{i}条" for i in range(n)]
    categories = [f"cat_{i % 5}" for i in range(n)]

    # 中文嵌入 (模拟)
    vecs_zh = rng.randn(n, DIM).astype(np.float32)
    vecs_zh /= np.linalg.norm(vecs_zh, axis=1, keepdims=True)

    # 英文嵌入 (模拟，不同随机种子)
    rng2 = np.random.RandomState(123)
    vecs_en = rng2.randn(n, DIM).astype(np.float32)
    vecs_en /= np.linalg.norm(vecs_en, axis=1, keepdims=True)

    table = pa.table({
        "id": ids,
        "text_content": texts,
        "category": categories,
        "embedding_zh": pa.FixedSizeListArray.from_arrays(vecs_zh.ravel(), DIM),
        "embedding_en": pa.FixedSizeListArray.from_arrays(vecs_en.ravel(), DIM),
    })
    lake.create_dataset("multilingual", table)
    print(f"  数据集: {n} 行, 2 个向量列 (embedding_zh, embedding_en)")

    # STEP 2: 集成搜索
    print("\nSTEP 2: 集成搜索 (加权 RRF)")
    rng_q = np.random.RandomState(42)
    q_zh = rng_q.randn(DIM).astype(np.float32).tolist()
    try:
        result = lake.ensemble_search(
            "multilingual", q_zh,
            columns=["embedding_zh", "embedding_en"],
            weights={"embedding_zh": 0.7, "embedding_en": 0.3},
            top_k=5,
        )
        print(f"  结果: {result.row_count} 条")
        print(f"  搜索列: {result.columns_searched}")
        print(f"  融合方法: {result.fusion_method}")
        for i in range(min(5, result.row_count)):
            t = result.table
            score = 0
            for col in ("_rrf_score", "_score", "_distance"):
                if col in t.column_names:
                    val = t.column(col)[i].as_py()
                    if val is not None:
                        score = val
                    break
            print(f"    #{i+1} {t.column('id')[i].as_py()}  score={score:.4f}")
    except Exception as e:
        print(f"  集成搜索跳过: {e}")

    # STEP 3: 单列对比
    print("\nSTEP 3: 单列搜索对比")
    for col in ("embedding_zh", "embedding_en"):
        try:
            result = lake.search("multilingual", q_zh, top_k=3, vector_column=col)
            print(f"  [{col}] {result.row_count} 条结果")
        except Exception as e:
            print(f"  [{col}] 跳过: {e}")

    # STEP 4: 均等权重
    print("\nSTEP 4: 均等权重集成搜索")
    try:
        result = lake.ensemble_search(
            "multilingual", q_zh,
            columns=["embedding_zh", "embedding_en"],
            top_k=3,
        )
        print(f"  结果: {result.row_count} 条 (均等权重)")
    except Exception as e:
        print(f"  跳过: {e}")

    print("\n  [全部 PASS]")
    if not no_cleanup:
        lake.shutdown()
        shutil.rmtree(base, ignore_errors=True)
        print("(已清理)")


if __name__ == "__main__":
    main()
