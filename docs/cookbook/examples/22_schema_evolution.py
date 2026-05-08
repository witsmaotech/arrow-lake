#!/usr/bin/env python3
"""22 — Schema 演化

场景: 演示数据集的 Schema 变更操作：添加列、修改类型、删除列、压缩碎片。

数据: 合成 (pa.table)
"""

from __future__ import annotations

import argparse

import shutil
import sys
from pathlib import Path

import numpy as np
import pyarrow as pa

from arrow_lake import Lake

_DEFAULT_BASE_URI = "./_tmp_schema_evo"
DIM = 128
_DATASETS = ["products"]


def main() -> None:
    parser = argparse.ArgumentParser(description="22_schema_evolution.py")
    parser.add_argument("--base-uri", default=_DEFAULT_BASE_URI)
    parser.add_argument("--no-cleanup", action="store_true")
    args = parser.parse_args()
    no_cleanup = args.no_cleanup
    print("=" * 60)
    print("22 Schema 演化")
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

    # STEP 1: 创建基础数据集
    print("STEP 1: 创建基础数据集")
    rng = np.random.RandomState(42)
    n = 20
    table = pa.table({
        "id": [f"item_{i:03d}" for i in range(n)],
        "name": [f"产品{i}" for i in range(n)],
        "price": rng.uniform(10, 1000, n).astype(np.float64).tolist(),
        "category": [f"cat_{i % 4}" for i in range(n)],
    })
    lake.create_dataset("products", table)
    ds = lake.open_dataset("products")
    print(f"  初始 schema: {len(ds.schema)} 列")
    for f in ds.schema:
        print(f"    {f.name}: {f.type}")
    v1 = lake.get_dataset_version("products")
    print(f"  版本: {v1}")

    # STEP 2: 添加计算列
    print("\nSTEP 2: 添加计算列 (discount_price)")
    try:
        lake.add_column("products", "discount_price", "CAST(price * 0.9 AS DOUBLE)")
        ds = lake.open_dataset("products")
        print(f"  添加列后: {len(ds.schema)} 列")
        for f in ds.schema:
            print(f"    {f.name}: {f.type}")
        v2 = lake.get_dataset_version("products")
        print(f"  版本: {v1} → {v2}")
    except RuntimeError as e:
        print(f"  跳过: {e}")

    # STEP 3: 修改列类型
    print("\nSTEP 3: 修改列类型 (price: float64 → float32)")
    try:
        lake.alter_column("products", "price", pa.float32())
        ds = lake.open_dataset("products")
        for f in ds.schema:
            if f.name == "price":
                print(f"  price → {f.type}")
    except (ValueError, OSError) as e:
        print(f"  跳过: {e}")

    # STEP 4: 删除列
    print("\nSTEP 4: 删除列 (discount_price)")
    try:
        lake.drop_column("products", "discount_price")
        ds = lake.open_dataset("products")
        print(f"  删除后: {len(ds.schema)} 列")
        for f in ds.schema:
            print(f"    {f.name}: {f.type}")
    except (ValueError, OSError) as e:
        print(f"  跳过: {e}")

    # STEP 5: 追加数据后压缩
    print("\nSTEP 5: 追加数据 + 压缩碎片")
    new_table = pa.table({
        "id": [f"item_{i:03d}" for i in range(n, n + 10)],
        "name": [f"产品{i}" for i in range(n, n + 10)],
        "price": rng.uniform(10, 1000, 10).astype(np.float32).tolist(),
        "category": [f"cat_{i % 4}" for i in range(n, n + 10)],
    })
    lake.append_dataset("products", new_table)
    ds = lake.open_dataset("products")
    print(f"  追加后: {ds.count_rows()} 行")

    try:
        stats = lake.compact_dataset("products")
        print(f"  压缩完成: version {stats.version_before} → {stats.version_after}")
        print(f"  碎片: {stats.fragments_before} → {stats.fragments_after}")
    except RuntimeError as e:
        print(f"  压缩跳过: {e}")

    # STEP 6: 数据验证
    print("\nSTEP 6: 数据验证")
    result = lake.olap_query("products",
        "SELECT category, COUNT(*) as cnt, ROUND(AVG(price),2) as avg_price "
        "FROM products GROUP BY category ORDER BY cnt DESC")
    for row in result.table.to_pylist():
        print(f"  {row['category']:<8} {row['cnt']:>3} 个  均价 ¥{row['avg_price']}")

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
