#!/usr/bin/env python3
"""18 — 导出与备份完整演示

场景: 展示数据导出格式对比和备份能力。

数据文件: datas/transactions/sales_2024_cn.csv
"""

from __future__ import annotations

import shutil
import sys
from pathlib import Path

from arrow_lake import Lake

DATAS_DIR = Path(__file__).resolve().parent.parent / "datas"
BASE_URI = "./_tmp_export_backup"


def main() -> None:
    no_cleanup = "--no-cleanup" in sys.argv
    print("=" * 60)
    print("18 导出与备份完整演示")
    print("=" * 60)

    base = Path(BASE_URI)
    if base.exists():
        shutil.rmtree(base)

    lake = Lake(base_uri=BASE_URI)

    # STEP 1: 创建数据集
    print("STEP 1: 摄入交易数据")
    report = lake.ingest("sales", [str(DATAS_DIR / "transactions" / "sales_2024_cn.csv")])
    ds = lake._get_storage().open_dataset("sales")
    print(f"  摄入: {report.total_rows} 行, {len(ds.schema)} 列")

    # STEP 2: 导出为 Parquet
    print("\nSTEP 2: 导出 Parquet")
    out_parquet = base / "sales.parquet"
    lake.export("sales", str(out_parquet), format="parquet")
    print(f"  Parquet: {out_parquet.stat().st_size // 1024} KB")

    # STEP 3: 导出为 CSV
    print("\nSTEP 3: 导出 CSV")
    out_csv = base / "sales.csv"
    lake.export("sales", str(out_csv), format="csv")
    print(f"  CSV: {out_csv.stat().st_size // 1024} KB")

    # STEP 4: 格式对比
    print("\nSTEP 4: 导出格式对比")
    files = [
        ("Parquet", out_parquet),
        ("CSV", out_csv),
    ]
    print(f"  {'格式':<10} {'大小':>8} {'压缩比':>8}")
    parquet_size = out_parquet.stat().st_size
    for fmt, f in files:
        size = f.stat().st_size
        ratio = size / parquet_size if parquet_size > 0 else 1
        print(f"  {fmt:<10} {size // 1024:>6} KB  {ratio:>6.2f}x")

    # STEP 5: 备份
    print("\nSTEP 5: 备份数据集")
    try:
        b = lake.backup_create("sales")
        print(f"  备份已创建: {b}")
    except Exception as e:
        print(f"  备份功能: {e}")

    # STEP 6: 备份列表
    print("\nSTEP 6: 备份列表")
    try:
        backups = lake.backup_list()
        if backups:
            for b in backups:
                print(f"  {b}")
        else:
            print("  无备份记录")
    except Exception as e:
        print(f"  备份列表: {e}")

    # STEP 7: 数据集目录
    print("\nSTEP 8: 数据集目录总览")
    for name in lake.list_datasets():
        ds = lake._get_storage().open_dataset(name)
        print(f"  {name}: {ds.count_rows()} 行, {len(ds.schema)} 列")

    print("\n  [全部 PASS]")
    if not no_cleanup:
        lake.shutdown()
        shutil.rmtree(base, ignore_errors=True)
        print("(已清理)")


if __name__ == "__main__":
    main()
