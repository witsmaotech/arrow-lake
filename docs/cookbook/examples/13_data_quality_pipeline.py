#!/usr/bin/env python3
"""13 — 数据清洗管道

场景: 自动化数据质量检测与清洗 — 噪声注入 → 检测 → 去重 → 对比报告。

数据: 合成 (含重复行、空值、异常值)
"""

from __future__ import annotations

import argparse

import shutil
import sys
import tempfile
from pathlib import Path

import numpy as np
import pyarrow as pa

from arrow_lake import Lake

_DEFAULT_BASE_URI = "./_tmp_quality_pipeline"
DATASET = "raw_data"
_DATASETS = ["raw_data"]
DIM = 64


def _make_noisy_table() -> pa.Table:
    """创建含各种噪声的 100 行数据"""
    rng = np.random.RandomState(42)
    n = 100
    vecs = rng.randn(n, DIM).astype(np.float32)
    ids = [f"row_{i:04d}" for i in range(n)]
    texts = [f"正常文本内容 第{i}条" for i in range(n)]
    categories = [f"cat_{i % 6}" for i in range(n)]

    # 空值: 8 行
    for idx in [3, 17, 31, 45, 59, 73, 87, 99]:
        texts[idx] = None

    # 完全重复: rows 60-64 == rows 0-4
    texts[60:65] = texts[0:5]
    ids[60:65] = ids[0:5]
    vecs[60:65] = vecs[0:5]

    # 近似重复: rows 65-69 ≈ rows 5-9
    texts[65:70] = texts[5:10]
    vecs[65:70] = vecs[5:10] + rng.randn(5, DIM).astype(np.float32) * 0.001

    # 异常值: rows 90-99 类别不同
    categories[90:] = ["异常品类"] * 10

    return pa.table({
        "id": ids,
        "text_content": texts,
        "category": categories,
        "score": rng.uniform(0, 100, n).tolist(),
        "embedding": pa.FixedSizeListArray.from_arrays(vecs.ravel(), DIM),
    })


def main() -> None:
    parser = argparse.ArgumentParser(description="13_data_quality_pipeline.py")
    parser.add_argument("--base-uri", default=_DEFAULT_BASE_URI)
    parser.add_argument("--no-cleanup", action="store_true")
    args = parser.parse_args()
    no_cleanup = args.no_cleanup
    print("=" * 60)
    print("13 数据清洗管道")
    print("=" * 60)

    # 使用临时目录避免 /app 权限问题
    if args.base_uri == _DEFAULT_BASE_URI:
        base = Path(tempfile.mkdtemp(prefix="quality_pipeline_"))
    else:
        base = Path(args.base_uri)
        if base.exists():
            shutil.rmtree(base)

    lake = Lake(base_uri=str(base))

    # 清理后端残留
    for ds in _DATASETS:
        try:
            lake.delete_dataset(ds)
        except Exception:
            pass

    # STEP 1: 创建含噪声数据
    print("STEP 1: 创建含噪声的合成数据")
    table = _make_noisy_table()
    lake.create_dataset(DATASET, table)
    nulls = sum(1 for v in table.column("text_content").to_pylist() if v is None)
    print(f"  总行数: {table.num_rows}, 空值: {nulls}")
    print(f"  品类分布: {dict(zip(*np.unique(table.column('category').to_pylist(), return_counts=True)))}")

    # STEP 2: 质量过滤
    print("\nSTEP 2: 质量过滤 (null_check)")
    qr = lake.quality_filter(DATASET, filters="null_check", mode="exclude")
    print(f"  通过: {qr.passed}, 拒绝: {qr.rejected}")

    # STEP 3: 精确去重
    print("\nSTEP 3: 精确去重")
    dr = lake.deduplicate(DATASET, strategy="exact", columns=["id", "text_content"], action="delete")
    print(f"  检出重复: {dr.duplicates_found} 行")

    # STEP 4: 感知去重
    print("\nSTEP 4: 感知去重")
    dr = lake.deduplicate(DATASET, strategy="simhash", threshold=0.95, columns=["text_content"], action="delete")
    print(f"  检出近似重复: {dr.duplicates_found} 行")

    # STEP 5: 导出清洗数据
    print("\nSTEP 5: 导出清洗后数据")
    out = base / "cleaned_data.parquet"
    lake.export(DATASET, str(out), format="parquet")
    catalog = lake.catalog()
    ds = catalog.datasets.get(DATASET)
    rows = ds.num_rows if ds else "?"
    print(f"  清洗后: {rows} 行 → {out} ({out.stat().st_size // 1024} KB)")

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
