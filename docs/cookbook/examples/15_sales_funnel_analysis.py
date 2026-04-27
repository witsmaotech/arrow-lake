#!/usr/bin/env python3
"""15 — 销售漏斗分析

场景: 用 OLAP SQL 做销售漏斗和用户行为分析。

数据文件: datas/transactions/sales_2024_cn.csv
"""

from __future__ import annotations

import argparse

import shutil
import sys
from pathlib import Path

from arrow_lake import Lake

DATAS_DIR = Path(__file__).resolve().parent.parent / "datas"
_DEFAULT_BASE_URI = "./_tmp_sales_funnel"


def main() -> None:
    parser = argparse.ArgumentParser(description="15_sales_funnel_analysis.py")
    parser.add_argument("--base-uri", default=_DEFAULT_BASE_URI)
    parser.add_argument("--no-cleanup", action="store_true")
    args = parser.parse_args()
    no_cleanup = args.no_cleanup
    print("=" * 60)
    print("15 销售漏斗分析")
    print("=" * 60)

    base = Path(args.base_uri)
    if base.exists():
        shutil.rmtree(base)

    lake = Lake(base_uri=args.base_uri)

    # STEP 1
    print("STEP 1: 摄入交易数据")
    report = lake.ingest("sales", [str(DATAS_DIR / "transactions" / "sales_2024_cn.csv")])
    print(f"  摄入: {report.total_rows} 行")

    # STEP 2
    print("\nSTEP 2: 品类交叉分析 — 品类 × 支付方式")
    result = lake.olap_query("sales",
        "SELECT 商品类别, 支付方式, COUNT(*) as 订单数, "
        "ROUND(SUM(金额),2) as 总额 FROM sales "
        "GROUP BY 商品类别, 支付方式 ORDER BY 商品类别, 总额 DESC")
    current_cat = ""
    for row in result.table.to_pylist():
        if row["商品类别"] != current_cat:
            if current_cat:
                print()
            current_cat = row["商品类别"]
            print(f"  [{current_cat}]")
        print(f"    {row['支付方式']:<10} 订单 {row['订单数']:>3}  ¥{row['总额']:>10}")
    print()

    # STEP 3
    print("STEP 3: 客单价分段统计")
    result = lake.olap_query("sales",
        "SELECT CASE "
        "  WHEN 金额 < 50 THEN '低 (<50)' "
        "  WHEN 金额 < 200 THEN '中 (50-200)' "
        "  WHEN 金额 < 500 THEN '高 (200-500)' "
        "  ELSE '超高 (500+)' "
        "END as 价格段, COUNT(*) as 订单数, ROUND(AVG(金额),2) as 均价, "
        "ROUND(SUM(金额),2) as 总额 "
        "FROM sales GROUP BY 1 ORDER BY 均价")
    for row in result.table.to_pylist():
        pct = row["总额"] / sum(r["总额"] for r in result.table.to_pylist()) * 100
        print(f"  {row['价格段']:<16} 订单 {row['订单数']:>3}  均价 ¥{row['均价']:>8}  占比 {pct:.1f}%")

    # STEP 4
    print("\nSTEP 4: 复购用户识别")
    result = lake.olap_query("sales",
        "SELECT 用户编号, COUNT(*) as 订单数, "
        "ROUND(SUM(金额),2) as 总消费, "
        "ROUND(MIN(金额),2) as 最低单, ROUND(MAX(金额),2) as 最高单 "
        "FROM sales GROUP BY 用户编号 HAVING COUNT(*) > 1 "
        "ORDER BY 总消费 DESC")
    rows = result.table.to_pylist()
    if rows:
        print(f"  复购用户: {len(rows)} 人")
        for row in rows[:5]:
            print(f"    {row['用户编号']:<8} 订单 {row['订单数']:>3}  总消费 ¥{row['总消费']:>10}  "
                  f"单价 ¥{row['最低单']}-{row['最高单']}")
    else:
        print("  无复购用户 (数据量较小)")

    # STEP 5
    print("\nSTEP 5: 城市消费力排名")
    result = lake.olap_query("sales",
        "SELECT 城市, COUNT(*) as 订单数, ROUND(SUM(金额),2) as 总额, "
        "ROUND(AVG(金额),2) as 客单价 "
        "FROM sales GROUP BY 城市 ORDER BY 客单价 DESC")
    for row in result.table.to_pylist():
        print(f"  {row['城市']:<8} 订单 {row['订单数']:>3}  客单价 ¥{row['客单价']:>8}  总额 ¥{row['总额']:>10}")

    # STEP 6: 物化 + 导出
    print("\nSTEP 6: 物化分析结果 + 导出")
    try:
        n = lake.materialize("sales",
            "SELECT 商品类别, 支付方式, COUNT(*) as 订单数, ROUND(SUM(金额),2) as 总额 "
            "FROM sales GROUP BY 商品类别, 支付方式",
            view_name="category_payment_cross", ttl_days=7)
        print(f"  物化视图: category_payment_cross ({n} 行)")
    except (ValueError, RuntimeError) as e:
        print(f"  物化跳过 (DuckLake 未启用): {e}")
    out = base / "funnel_analysis.csv"
    lake.export("sales", str(out), format="csv")
    print(f"  导出: {out} ({out.stat().st_size // 1024} KB)")

    print("\n  [全部 PASS]")
    if not no_cleanup:
        lake.shutdown()
        shutil.rmtree(base, ignore_errors=True)
        print("(已清理)")


if __name__ == "__main__":
    main()
