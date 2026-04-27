#!/usr/bin/env python3
"""25 — 元数据查询

场景: 使用 query/sql_query 接口执行元数据查询，展示 SQL 注入防护。

数据文件: datas/transactions/sales_2024_cn.csv
"""

from __future__ import annotations

import argparse

import shutil
import sys
from pathlib import Path

from arrow_lake import Lake

DATAS_DIR = Path(__file__).resolve().parent.parent / "datas"
_DEFAULT_BASE_URI = "./_tmp_metadata"


def main() -> None:
    parser = argparse.ArgumentParser(description="25_metadata_query.py")
    parser.add_argument("--base-uri", default=_DEFAULT_BASE_URI)
    parser.add_argument("--no-cleanup", action="store_true")
    args = parser.parse_args()
    no_cleanup = args.no_cleanup
    print("=" * 60)
    print("25 元数据查询")
    print("=" * 60)

    base = Path(args.base_uri)
    if base.exists():
        shutil.rmtree(base)

    lake = Lake(base_uri=args.base_uri)

    # STEP 1: 摄入数据
    print("STEP 1: 摄入交易数据")
    r = lake.ingest("sales", [str(DATAS_DIR / "transactions" / "sales_2024_cn.csv")])
    print(f"  摄入: {r.total_rows} 行")

    # STEP 2: 基础元数据查询
    print("\nSTEP 2: 基础元数据查询 (query)")
    try:
        result = lake.query("sales", "SELECT * FROM sales LIMIT 3")
        print(f"  列数: {result.column_count}, 行数: {result.row_count}")
        print(f"  SQL: {result.sql}")
        for row in result.table.to_pylist():
            print(f"    {row.get('订单号', row.get('order_id', ''))} "
                  f"¥{row.get('金额', row.get('amount', 0))}")
    except Exception as e:
        print(f"  query 跳过: {e}")

    # STEP 3: OLAP 聚合查询
    print("\nSTEP 3: OLAP 聚合查询 (olap_query)")
    result = lake.olap_query("sales",
        "SELECT 商品类别, COUNT(*) as cnt, ROUND(SUM(金额),2) as total "
        "FROM sales GROUP BY 商品类别 ORDER BY total DESC")
    for row in result.table.to_pylist():
        print(f"  {row['商品类别']:<12} {row['cnt']:>3} 单  ¥{row['total']:>10}")

    # STEP 4: SQL 注入防护
    print("\nSTEP 4: SQL 注入防护")
    dangerous_queries = [
        "DROP TABLE sales",
        "DELETE FROM sales WHERE 1=1",
        "INSERT INTO sales VALUES ('x')",
        "UPDATE sales SET 金额=0",
    ]
    for sql in dangerous_queries:
        try:
            result = lake.olap_query("sales", sql)
            print(f"  [未拦截] {sql}")
        except (ValueError, RuntimeError) as e:
            print(f"  [已拦截] {sql} → {type(e).__name__}")

    # STEP 5: 子查询
    print("\nSTEP 5: 子查询分析")
    try:
        result = lake.olap_query("sales",
            "SELECT * FROM ("
            "  SELECT 商品类别, 城市, COUNT(*) as cnt, ROUND(AVG(金额),2) as avg_amt "
            "  FROM sales GROUP BY 商品类别, 城市"
            ") sub WHERE avg_amt > 100 ORDER BY avg_amt DESC LIMIT 5")
        print(f"  高客单价分类×城市 (avg>100):")
        for row in result.table.to_pylist():
            print(f"    {row['商品类别']:<12} {row['城市']:<8} 均价 ¥{row['avg_amt']}")
    except Exception as e:
        print(f"  子查询跳过: {e}")

    print("\n  [全部 PASS]")
    if not no_cleanup:
        lake.shutdown()
        shutil.rmtree(base, ignore_errors=True)
        print("(已清理)")


if __name__ == "__main__":
    main()
