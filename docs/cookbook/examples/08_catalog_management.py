#!/usr/bin/env python3
"""08 — 数据集目录管理

演示数据集完整生命周期: 创建 → 列出 → 详情 → 导出 → 删除。

数据: 合成
"""

from __future__ import annotations

import argparse

import shutil
import sys
from pathlib import Path

import numpy as np
import pyarrow as pa

from arrow_lake import Lake

_DEFAULT_BASE_URI = "./_tmp_catalog"
_DATASETS = ["users", "products", "orders"]


def main() -> None:
    parser = argparse.ArgumentParser(description="08_catalog_management.py")
    parser.add_argument("--base-uri", default=_DEFAULT_BASE_URI)
    parser.add_argument("--no-cleanup", action="store_true")
    args = parser.parse_args()
    no_cleanup = args.no_cleanup
    print("=" * 60)
    print("08 数据集目录管理")
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

    # --- STEP 1: 创建数据集 ---
    print("STEP 1: 创建三个数据集")
    for name, count, cols in [
        ("users", 10, ["id", "name", "age"]),
        ("products", 20, ["id", "title", "price", "category"]),
        ("orders", 50, ["id", "user_id", "product_id", "amount"]),
    ]:
        table = pa.table({
            "id": [f"{name[:3]}_{i:03d}" for i in range(count)],
            **{c: [f"val_{i}" for i in range(count)] for c in cols[1:]},
        })
        lake.create_dataset(name, table)
        print(f"  {name}: {count} 行")
    print("  [PASS]\n")

    # --- STEP 2: 列出数据集 ---
    print("STEP 2: 列出全部数据集")
    datasets = lake.list_datasets()
    for name in datasets:
        print(f"  - {name}")
    missing = [ds for ds in _DATASETS if ds not in datasets]
    if missing:
        print(f"  [FAIL] Expected datasets missing: {missing}")
        return
    print("  [PASS]\n")

    # STEP 3: 查看详情
    print("STEP 3: 数据集详情")
    for name in datasets:
        ds = lake.open_dataset(name)
        print(f"  {name}: {ds.count_rows()} 行, {len(ds.schema)} 列")
    print("  [PASS]\n")

    # --- STEP 4: 导出 ---
    print("STEP 4: 导出数据集")
    out = (base / "orders_export.parquet").resolve()
    lake.export("orders", str(out), format="parquet")
    print(f"  orders → {out} ({out.stat().st_size // 1024} KB)")
    print("  [PASS]\n")

    # --- STEP 5: 删除 ---
    print("STEP 5: 删除数据集")
    lake.delete_dataset("orders")
    remaining = lake.list_datasets()
    if "orders" in remaining:
        print(f"  [FAIL] orders still present: {remaining}")
        return
    print(f"  已删除 orders, 剩余: {remaining}")
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
    print("08 完成")
    print("=" * 60)


if __name__ == "__main__":
    main()
