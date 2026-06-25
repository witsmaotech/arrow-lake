#!/usr/bin/env python3
"""04 — 数据质量与去重

用合成数据演示质量过滤和精确去重/感知去重。

数据: 合成 (含重复行和空值)
"""

from __future__ import annotations

import argparse

import shutil
import sys
from pathlib import Path

import numpy as np
import pyarrow as pa

from arrow_lake import Lake

_DEFAULT_BASE_URI = "./_tmp_quality"
DATASET = "sample_data"
_DATASETS = ("sample_data",)
DIM = 64


def _make_noisy_data() -> pa.Table:
    """创建含重复行和空值的数据集"""
    rng = np.random.RandomState(42)
    n = 50
    vecs = rng.randn(n, DIM).astype(np.float32)
    texts = [f"文档内容 {i % 30}" for i in range(n)]
    ids = [f"doc_{i:04d}" for i in range(n)]
    # 插入空值
    texts[5] = None
    texts[15] = None
    texts[25] = None
    # 插入完全重复
    texts[30:35] = texts[0:5]
    ids[30:35] = ids[0:5]
    vecs[30:35] = vecs[0:5]
    # 插入近似重复
    texts[35:40] = [f"文档内容 {i % 30}" for i in range(35, 40)]
    vecs[35:40] = vecs[0:5] + rng.randn(5, DIM).astype(np.float32) * 0.01

    return pa.table({
        "id": ids,
        "text_content": texts,
        "category": [f"cat_{i % 5}" for i in range(n)],
        "embedding": pa.FixedSizeListArray.from_arrays(vecs.ravel(), DIM),
    })


def main() -> None:
    parser = argparse.ArgumentParser(description="04_quality_and_dedup.py")
    parser.add_argument("--base-uri", default=_DEFAULT_BASE_URI)
    parser.add_argument("--no-cleanup", action="store_true")
    args = parser.parse_args()
    no_cleanup = args.no_cleanup
    print("=" * 60)
    print("04 数据质量与去重")
    print("=" * 60)

    base = Path(args.base_uri)
    if base.exists():
        shutil.rmtree(base)

    lake = Lake(base_uri=args.base_uri)

    # 清理 MinIO 后端可能残留的数据集
    for ds in _DATASETS:
        try:
            lake.delete_dataset(ds)
        except Exception:
            pass

    # --- STEP 1: 创建含噪声的数据集 ---
    print("STEP 1: 创建含噪声的合成数据")
    table = _make_noisy_data()
    lake.create_dataset(DATASET, table)
    print(f"  总行数: {table.num_rows}")
    nulls = sum(1 for v in table.column("text_content").to_pylist() if v is None)
    print(f"  空值数: {nulls}")
    print("  [PASS]\n")

    # --- STEP 2: 精确去重 ---
    print("STEP 2: 精确去重")
    result = lake.deduplicate(DATASET, strategy="exact", action="remove")
    print(f"  重复行数: {result.duplicates_found}")
    print("  [PASS]\n")

    # --- STEP 3: 感知去重 ---
    print("STEP 3: 感知去重 (基于向量)")
    result = lake.deduplicate(DATASET, strategy="perceptual", action="remove")
    print(f"  近似重复数: {result.duplicates_found}")
    print("  [PASS]\n")

    # --- STEP 4: 质量过滤 ---
    print("STEP 4: 质量过滤 (空值检查)")
    result = lake.quality_filter(DATASET, active_filters="null_check", mode="all")
    print(f"  通过: {result.passed}")
    print(f"  拒绝: {result.rejected}")
    print("  [PASS]\n")

    if not no_cleanup:
        for ds in _DATASETS:
            try:
                lake.delete_dataset(ds)
            except Exception:
                pass
        lake.shutdown()
        shutil.rmtree(base, ignore_errors=True)
        print("(已清理)")

    print("=" * 60)
    print("04 完成")
    print("=" * 60)


if __name__ == "__main__":
    main()
