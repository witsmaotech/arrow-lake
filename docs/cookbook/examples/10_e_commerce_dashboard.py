#!/usr/bin/env python3
"""10 — 电商数据看板

场景: 从交易数据构建多维分析看板，展示品类/城市/支付方式等多维聚合。

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
_DEFAULT_BASE_URI = "./_tmp_ecommerce"


def main() -> None:
    parser = argparse.ArgumentParser(description="10_e_commerce_dashboard.py")
    parser.add_argument("--base-uri", default=_DEFAULT_BASE_URI)
    parser.add_argument("--no-cleanup", action="store_true")
    args = parser.parse_args()
    no_cleanup = args.no_cleanup
    print("=" * 60)
    print("10 电商数据看板")
    print("=" * 60)

    base = Path(args.base_uri)
    if base.exists():
        shutil.rmtree(base)

    config = ArrowLakeConfig()
    config.olap.ducklake_enabled = True
    lake = Lake(base_uri=args.base_uri, config=config)

    # 清理后端残留
    _DATASETS = ["sales"]
    for ds in _DATASETS:
        try:
            lake.delete_dataset(ds)
        except Exception:
            pass

    # STEP 1
    print("STEP 1: 摄入交易数据")
    report = lake.ingest("sales", [str(DATAS_DIR / "transactions" / "sales_2024_cn.csv")])
    print(f"  摄入: {report.total_rows} 行\n")

    # STEP 2
    print("STEP 2: 品类销售额排名")
    result = lake.olap_query("sales",
        "SELECT 商品类别, COUNT(*) as 订单数, ROUND(SUM(金额),2) as 总额, "
        "ROUND(AVG(金额),2) as 均价 FROM sales "
        "GROUP BY 商品类别 ORDER BY 总额 DESC")
    print(f"  {'品类':<12} {'订单':>5} {'总额':>10} {'均价':>8}")
    for row in result.table.to_pylist():
        print(f"  {row['商品类别']:<12} {row['订单数']:>5} {row['总额']:>10} {row['均价']:>8}")
    print()

    # STEP 3
    print("STEP 3: 城市订单分布")
    result = lake.olap_query("sales",
        "SELECT 城市, COUNT(*) as 订单数, ROUND(SUM(金额),2) as 总额 "
        "FROM sales GROUP BY 城市 ORDER BY 订单数 DESC")
    for row in result.table.to_pylist():
        print(f"  {row['城市']:<8} 订单 {row['订单数']:>3}  ¥{row['总额']:>10}")
    print()

    # STEP 4
    print("STEP 4: 支付方式占比")
    result = lake.olap_query("sales",
        "SELECT 支付方式, COUNT(*) as 订单数, ROUND(SUM(金额),2) as 总额 "
        "FROM sales GROUP BY 支付方式 ORDER BY 总额 DESC")
    for row in result.table.to_pylist():
        print(f"  {row['支付方式']:<12} 订单 {row['订单数']:>3}  ¥{row['总额']:>10}")
    print()

    # STEP 5
    print("STEP 5: Top 5 高消费用户")
    result = lake.olap_query("sales",
        "SELECT 用户编号, COUNT(*) as 订单数, ROUND(SUM(金额),2) as 总消费 "
        "FROM sales GROUP BY 用户编号 ORDER BY 总消费 DESC LIMIT 5")
    print(f"  {'用户':<8} {'订单':>5} {'总消费':>10}")
    for row in result.table.to_pylist():
        print(f"  {row['用户编号']:<8} {row['订单数']:>5} ¥{row['总消费']:>10}")
    print()

    # STEP 6
    print("STEP 6: 物化 '品类月报' 视图")
    try:
        view_id = lake.materialize("sales",
            "SELECT 商品类别, COUNT(*) as 订单数, ROUND(SUM(金额),2) as 总额 "
            "FROM sales GROUP BY 商品类别",
            view_name="category_monthly", ttl_days=30)
        print(f"  物化视图: category_monthly (id={view_id})")
    except Exception as e:
        print(f"  跳过 (DuckLake 未启用): {e}")

    # STEP 7
    print("\nSTEP 7: 导出看板数据")
    out = (base / "dashboard.csv").resolve()
    lake.export("sales", str(out), format="csv")
    print(f"  导出: {out} ({out.stat().st_size // 1024} KB)")

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
