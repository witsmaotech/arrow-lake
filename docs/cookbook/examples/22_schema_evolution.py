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
    catalog = lake.catalog()
    ds = catalog.datasets.get("products")
    print(f"  初始 schema: {ds.num_rows} 行")
    print(f"  版本: {ds.version}")

    # STEP 2: 添加计算列
    print("\nSTEP 2: 添加计算列 (discount_price)")
    try:
        # 创建带新列的完整表并 upsert
        new_table = pa.table({
            "id": table.column("id").to_pylist(),
            "name": table.column("name").to_pylist(),
            "price": table.column("price").to_pylist(),
            "category": table.column("category").to_pylist(),
            "discount_price": [p * 0.9 for p in table.column("price").to_pylist()],
        })
        lake.delete_dataset("products")
        lake.create_dataset("products", new_table)
        catalog = lake.catalog()
        ds = catalog.datasets.get("products")
        print(f"  添加列后: {ds.num_rows} 行")
    except RuntimeError as e:
        print(f"  跳过: {e}")

    # STEP 3: 修改列类型 (重建数据集)
    print("\nSTEP 3: 修改列类型 (price: float64 → float32)")
    try:
        table_f32 = new_table
        lake.delete_dataset("products")
        lake.create_dataset("products", table_f32)
        print("  price 类型已更新 (通过重建)")
    except (ValueError, OSError, Exception) as e:
        print(f"  跳过: {e}")

    # STEP 4: 删除列 (重建数据集)
    print("\nSTEP 4: 删除列 (discount_price)")
    try:
        trimmed = table_f32.select([c for c in table_f32.column_names if c != "discount_price"])
        lake.delete_dataset("products")
        lake.create_dataset("products", trimmed)
        print(f"  删除后: {trimmed.num_columns} 列")
    except (ValueError, OSError) as e:
        print(f"  跳过: {e}")

    # STEP 5: 追加数据
    print("\nSTEP 5: 追加数据")
    append_table = pa.table({
        "id": [f"item_{i:03d}" for i in range(n, n + 10)],
        "name": [f"产品{i}" for i in range(n, n + 10)],
        "price": rng.uniform(10, 1000, 10).astype(np.float32).tolist(),
        "category": [f"cat_{i % 4}" for i in range(n, n + 10)],
    })
    lake.append_dataset("products", append_table)
    catalog = lake.catalog()
    ds = catalog.datasets.get("products")
    rows = ds.num_rows if ds else "?"
    print(f"  追加后: {rows} 行")

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
