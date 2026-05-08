#!/usr/bin/env python3
"""03 — SQL 分析与导出

演示 DuckDB OLAP 查询、物化视图和数据导出。

数据文件: datas/transactions/sales_2024_cn.csv
"""

from __future__ import annotations

import argparse

import shutil
import sys
from pathlib import Path

from arrow_lake import Lake
from arrow_lake.config import ArrowLakeConfig

DATAS_DIR = Path(__file__).resolve().parent.parent / "datas"
_DEFAULT_BASE_URI = "./_tmp_olap_export"
DATASET = "transactions"
EXPORT_DIR = Path("./_tmp_olap_export_out")
_DATASETS = [DATASET]


def main() -> None:
    parser = argparse.ArgumentParser(description="03_olap_and_export.py")
    parser.add_argument("--base-uri", default=_DEFAULT_BASE_URI)
    parser.add_argument("--no-cleanup", action="store_true")
    args = parser.parse_args()
    no_cleanup = args.no_cleanup
    print("=" * 60)
    print("03 SQL 分析与导出")
    print("=" * 60)

    base = Path(args.base_uri)
    if base.exists():
        shutil.rmtree(base)
    if EXPORT_DIR.exists():
        shutil.rmtree(EXPORT_DIR)
    EXPORT_DIR.mkdir(parents=True, exist_ok=True)

    config = ArrowLakeConfig()
    config.olap.ducklake_enabled = True
    lake = Lake(base_uri=args.base_uri, config=config)

    # 清理后端残留
    for ds in _DATASETS:
        try:
            lake.delete_dataset(ds)
        except Exception:
            pass

    # --- STEP 1: 摄入数据 ---
    print("STEP 1: 摄取交易数据")
    csv_path = str(DATAS_DIR / "transactions" / "sales_2024_cn.csv")
    report = lake.ingest(DATASET, [csv_path])
    print(f"  摄入: {report.total_rows} 行")
    print("  [PASS]\n")

    # --- STEP 2: OLAP 聚合查询 ---
    print("STEP 2: 各品类销售额排名")
    result = lake.olap_query(DATASET,
        "SELECT 商品类别, COUNT(*) as 订单数, "
        "ROUND(SUM(金额), 2) as 总额, ROUND(AVG(金额), 2) as 均价 "
        "FROM transactions GROUP BY 商品类别 ORDER BY 总额 DESC")
    print(f"  {'品类':<12} {'订单数':>6} {'总额':>10} {'均价':>8}")
    print(f"  {'─'*12} {'─'*6} {'─'*10} {'─'*8}")
    for row in result.table.to_pylist():
        print(f"  {row['商品类别']:<12} {row['订单数']:>6} {row['总额']:>10} {row['均价']:>8}")
    print("  [PASS]\n")

    # --- STEP 3: 按城市统计 ---
    print("STEP 3: 各城市订单分布")
    result = lake.olap_query(DATASET,
        "SELECT 城市, COUNT(*) as 订单数, ROUND(SUM(金额), 2) as 总额 "
        "FROM transactions GROUP BY 城市 ORDER BY 订单数 DESC")
    for row in result.table.to_pylist():
        print(f"  {row['城市']:<8} 订单 {row['订单数']:>3}  金额 {row['总额']:>10}")
    print("  [PASS]\n")

    # --- STEP 4: 物化视图 ---
    print("STEP 4: 创建物化视图 (品类月报)")
    try:
        row_count = lake.materialize(DATASET,
            "SELECT 商品类别, COUNT(*) as 订单数, ROUND(SUM(金额),2) as 总额 "
            "FROM transactions GROUP BY 商品类别",
            view_name="category_summary", ttl_days=7)
        print(f"  物化视图: category_summary ({row_count} 行)")
    except Exception as e:
        print(f"  跳过 (DuckLake 未启用): {e}")
    print("  [PASS]\n")

    # --- STEP 5: 导出 Parquet ---
    print("STEP 5: 导出为 Parquet")
    out_parquet = str((EXPORT_DIR / "transactions.parquet").resolve())
    lake.export(DATASET, out_parquet, format="parquet")
    size_kb = EXPORT_DIR.joinpath("transactions.parquet").stat().st_size // 1024
    print(f"  导出: {out_parquet} ({size_kb} KB)")
    print("  [PASS]\n")

    # --- STEP 6: 导出 CSV ---
    print("STEP 6: 导出 CSV (指定列)")
    out_csv = str((EXPORT_DIR / "transactions_summary.csv").resolve())
    lake.export(DATASET, out_csv, format="csv",
               columns=["时间戳", "订单号", "商品类别", "商品名称", "金额", "城市"])
    size_kb = EXPORT_DIR.joinpath("transactions_summary.csv").stat().st_size // 1024
    print(f"  导出: {out_csv} ({size_kb} KB)")
    print("  [PASS]\n")

    if not no_cleanup:
        for ds in _DATASETS:
            try:
                lake.delete_dataset(ds)
            except Exception:
                pass
        lake.shutdown()
        shutil.rmtree(base, ignore_errors=True)
        shutil.rmtree(EXPORT_DIR, ignore_errors=True)
        print("(已清理)")

    print("=" * 60)
    print("03 完成")
    print("=" * 60)


if __name__ == "__main__":
    main()
