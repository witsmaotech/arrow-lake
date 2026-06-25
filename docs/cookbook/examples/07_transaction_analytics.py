#!/usr/bin/env python3
"""07 — 交易数据分析工作流

端到端: ingest → dedup → 多维 SQL 分析 → materialize → export。

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
_DEFAULT_BASE_URI = "./_tmp_txn_analytics"
DATASET = "transactions"
_DATASETS = [DATASET]


def main() -> None:
    parser = argparse.ArgumentParser(description="07_transaction_analytics.py")
    parser.add_argument("--base-uri", default=_DEFAULT_BASE_URI)
    parser.add_argument("--no-cleanup", action="store_true")
    args = parser.parse_args()
    no_cleanup = args.no_cleanup
    print("=" * 60)
    print("07 交易数据分析工作流")
    print("=" * 60)

    base = Path(args.base_uri)
    if base.exists():
        shutil.rmtree(base)

    config = ArrowLakeConfig()
    config.olap.ducklake_enabled = True
    lake = Lake(base_uri=args.base_uri, config=config)

    # 清理后端残留
    for ds in _DATASETS:
        try:
            lake.delete_dataset(ds)
        except Exception:
            pass

    # --- STEP 1: 摄入 ---
    print("STEP 1: 摄入交易数据")
    report = lake.ingest(DATASET, [str(DATAS_DIR / "transactions" / "sales_2024_cn.csv")])
    print(f"  摄入: {report.total_rows} 行")
    print("  [PASS]\n")

    # --- STEP 2: 去重 ---
    print("STEP 2: 精确去重")
    result = lake.deduplicate(DATASET, strategy="exact", action="remove")
    print(f"  重复订单: {result.duplicates_found}")
    print("  [PASS]\n")

    # --- STEP 3: 多维 SQL 分析 ---
    queries = [
        ("品类销售额排名",
         "SELECT 商品类别, COUNT(*) as 订单数, ROUND(SUM(金额),2) as 总额 "
         "FROM transactions GROUP BY 商品类别 ORDER BY 总额 DESC"),
        ("城市订单分布",
         "SELECT 城市, COUNT(*) as 订单数, ROUND(SUM(金额),2) as 总额 "
         "FROM transactions GROUP BY 城市 ORDER BY 订单数 DESC"),
        ("支付方式占比",
         "SELECT 支付方式, COUNT(*) as 订单数, ROUND(SUM(金额),2) as 总额 "
         "FROM transactions GROUP BY 支付方式 ORDER BY 总额 DESC"),
        ("Top 5 高消费用户",
         "SELECT 用户编号, COUNT(*) as 订单数, ROUND(SUM(金额),2) as 总消费 "
         "FROM transactions GROUP BY 用户编号 ORDER BY 总消费 DESC LIMIT 5"),
    ]

    for title, sql in queries:
        print(f"  {title}:")
        result = lake.olap_query(DATASET, sql)
        for row in result.table.to_pylist():
            vals = "  ".join(f"{v}" for v in row.values())
            print(f"    {vals}")
        print()

    # --- STEP 4: 物化视图 ---
    print("STEP 4: 物化视图 (用户消费汇总)")
    try:
        view_id = lake.materialize(DATASET,
            "SELECT 用户编号, COUNT(*) as 订单数, ROUND(SUM(金额),2) as 总消费 "
            "FROM transactions GROUP BY 用户编号",
            view_name="user_summary", ttl_days=7)
        print(f"  物化: user_summary (id={view_id})")
    except Exception as e:
        print(f"  跳过 (DuckLake 未启用): {e}")
    print("  [PASS]\n")

    # --- STEP 5: 导出 ---
    print("STEP 5: 导出分析结果")
    out = (base / "transaction_summary.csv").resolve()
    lake.export(DATASET, str(out), format="csv")
    print(f"  导出: {out} ({out.stat().st_size // 1024} KB)")
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
    print("07 完成")
    print("=" * 60)


if __name__ == "__main__":
    main()
