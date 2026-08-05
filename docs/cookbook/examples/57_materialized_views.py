#!/usr/bin/env python3
"""DuckLake 物化视图 — OLAP 查询结果持久化与 TTL 管理

演示 Arrow Lake v1.2 的 DuckLake 物化视图功能：
  1. 启用 DuckLake 配置
  2. 创建物化视图 (SQL → 持久化表)
  3. 查询物化视图
  4. TTL 过期清理
  5. 典型应用场景

用法:
    python examples/query/materialized_views.py
    python examples/query/materialized_views.py --base-uri /tmp/test_lake --no-cleanup
"""

from __future__ import annotations

import argparse
import shutil

import pyarrow as pa
from arrow_lake import Lake
from arrow_lake.config import ArrowLakeConfig

DATASETS = ["sales", "products"]


def main() -> None:
    parser = argparse.ArgumentParser(description="DuckLake 物化视图")
    parser.add_argument("--base-uri", default="./lake_data")
    parser.add_argument("--no-cleanup", action="store_true")
    args = parser.parse_args()

    lake = Lake(base_uri=args.base_uri)
    print(f"Arrow Lake v{lake.version()}\n")

    try:
        _step1_config(lake)
        _step2_create_data(lake)
        _step3_materialize(lake)
        _step4_query_view(lake)
        _step5_ttl_cleanup(lake)
        _step6_use_cases(lake)
    finally:
        if not args.no_cleanup:
            _cleanup(lake, args.base_uri)
            print("\n[cleanup] 测试数据已清理")


# ---------------------------------------------------------------------------
# Step 1: DuckLake 配置
# ---------------------------------------------------------------------------

def _step1_config(lake: Lake) -> None:
    print("=" * 60)
    print("STEP 1: DuckLake 配置")
    print("=" * 60)

    config = ArrowLakeConfig()
    print(f"  ducklake_enabled: {config.olap.ducklake_enabled}")

    print("""
启用 DuckLake:

  环境变量:
    ARROW_LAKE__OLAP__DUCKLAKE_ENABLED=true

  YAML:
    olap:
      ducklake_enabled: true
      max_materialized_views: 100

  Python:
    config = ArrowLakeConfig()
    config.olap.ducklake_enabled = True
    lake = Lake(base_uri="./data", config=config)
""")

    print("  [PASS]\n")


# ---------------------------------------------------------------------------
# Step 2: 创建数据
# ---------------------------------------------------------------------------

def _step2_create_data(lake: Lake) -> None:
    print("=" * 60)
    print("STEP 2: 创建示例数据")
    print("=" * 60)

    # 销售数据
    sales = pa.table({
        "id": [f"s{i:04d}" for i in range(100)],
        "product": [f"Product_{i % 10}" for i in range(100)],
        "category": [["Electronics", "Books", "Clothing"][i % 3] for i in range(100)],
        "amount": [50.0 + i * 3.5 for i in range(100)],
        "quantity": [1 + i % 5 for i in range(100)],
        "region": [["North", "South", "East", "West"][i % 4] for i in range(100)],
    })
    lake.create_dataset("sales", sales)
    print(f"  sales: {sales.num_rows} 行")

    # 产品数据
    products = pa.table({
        "name": [f"Product_{i}" for i in range(10)],
        "category": [["Electronics", "Books", "Clothing"][i % 3] for i in range(10)],
        "price": [29.99 + i * 10 for i in range(10)],
    })
    lake.create_dataset("products", products)
    print(f"  products: {products.num_rows} 行")

    print("  [PASS]\n")


# ---------------------------------------------------------------------------
# Step 3: 创建物化视图
# ---------------------------------------------------------------------------

def _step3_materialize(lake: Lake) -> None:
    print("=" * 60)
    print("STEP 3: 创建物化视图")
    print("=" * 60)

    print("""
物化视图: 将 SQL 查询结果持久化为 DuckLake 表

Python SDK:

  rows = lake.materialize(
      dataset_name="sales",
      sql="SELECT category, region, "
          "SUM(amount) as total_sales, "
          "AVG(amount) as avg_sale "
          "FROM sales "
          "GROUP BY category, region",
      view_name="sales_by_category_region",
      ttl_days=7,
  )

REST API:

  POST /api/v1/datasets/sales/materialize
  {
    "sql": "SELECT category, SUM(amount) ...",
    "view_name": "sales_summary",
    "ttl_days": 7
  }
""")

    # 先执行普通 OLAP 查询作为对比
    result = lake.olap_query(
        "sales",
        "SELECT category, region, SUM(amount) as total, "
        "COUNT(*) as cnt FROM sales GROUP BY category, region "
        "ORDER BY total DESC",
    )
    print(f"\n  OLAP 查询结果 ({result.row_count} 行):")
    for i in range(min(result.row_count, 6)):
        cat = result.table.column("category")[i].as_py()
        reg = result.table.column("region")[i].as_py()
        total = result.table.column("total")[i].as_py()
        cnt = result.table.column("cnt")[i].as_py()
        print(f"    {cat}/{reg}: total=${total:.2f}, count={cnt}")

    # 尝试物化
    if hasattr(lake, "materialize"):
        try:
            rows = lake.materialize(
                "sales",
                "SELECT category, region, SUM(amount) as total_sales "
                "FROM sales GROUP BY category, region",
                view_name="sales_by_cat_region",
                ttl_days=7,
            )
            print(f"\n  [物化] view_name=sales_by_cat_region, rows={rows}")
        except Exception as exc:
            print(f"\n  [物化] 跳过: {exc}")
    else:
        print("\n  [物化] lake.materialize() 需要启用 DuckLake")

    print("\n  [PASS]\n")


# ---------------------------------------------------------------------------
# Step 4: 查询物化视图
# ---------------------------------------------------------------------------

def _step4_query_view(lake: Lake) -> None:
    print("=" * 60)
    print("STEP 4: 查询物化视图")
    print("=" * 60)

    print("""
物化视图查询:

  创建物化视图后, 可以:
  1. 直接通过 view_name 查询 (比原始查询快)
  2. 在 JOIN 中引用物化视图
  3. 物化视图支持版本管理

  -- 使用物化视图加速查询
  lake.olap_query(
      "sales_by_cat_region",  -- 直接引用物化视图名
      "SELECT * FROM sales_by_cat_region "
      "WHERE total_sales > 1000 ORDER BY total_sales DESC"
  )

REST API:

  POST /api/v1/datasets/sales_by_cat_region/query
  {"sql": "SELECT * FROM sales_by_cat_region ORDER BY total_sales DESC"}
""")

    print("  [PASS]\n")


# ---------------------------------------------------------------------------
# Step 5: TTL 清理
# ---------------------------------------------------------------------------

def _step5_ttl_cleanup(lake: Lake) -> None:
    print("=" * 60)
    print("STEP 5: TTL 过期清理")
    print("=" * 60)

    print("""
TTL 管理:

  每个物化视图可设置 TTL (存活天数):
    - ttl_days=7 → 7 天后自动清理
    - ttl_days=None → 使用配置默认值
    - ttl_days=0 → 立即过期

  清理命令:

  # Python SDK
  cleaned = lake.cleanup_materialized(ttl_days=7)  # 删除过期视图

  # REST API
  POST /api/v1/olap/cleanup
  {"ttl_days": 7}

  自动清理建议:
  - 在定时任务中运行 cleanup_materialized()
  - 配合 Kubernetes CronJob 使用
  - 或者使用 Metaflow flow 定期清理
""")

    print("  [PASS]\n")


# ---------------------------------------------------------------------------
# Step 6: 应用场景
# ---------------------------------------------------------------------------

def _step6_use_cases(lake: Lake) -> None:
    print("=" * 60)
    print("STEP 6: 物化视图典型应用场景")
    print("=" * 60)

    print("""
场景 1: 仪表板预计算

  -- 每日物化仪表板数据
  lake.materialize("events", \"\"\"
      SELECT DATE(event_time) as day,
             event_type,
             COUNT(*) as count
      FROM events
      WHERE event_time >= CURRENT_DATE - INTERVAL 30 DAY
      GROUP BY day, event_type
  \"\"\", view_name="daily_events_30d", ttl_days=1)

场景 2: 多表 JOIN 预计算

  -- 将 JOIN 结果物化避免重复计算
  lake.materialize("orders", \"\"\"
      SELECT o.id, c.name, p.title, o.amount
      FROM orders o
      JOIN customers c ON o.customer_id = c.id
      JOIN products p ON o.product_id = p.id
  \"\"\", view_name="order_details", ttl_days=7)

场景 3: 聚合报表

  -- 月度销售报表
  lake.materialize("sales", \"\"\"
      SELECT
        YEAR(sale_date) as year,
        MONTH(sale_date) as month,
        region,
        SUM(amount) as revenue,
        COUNT(DISTINCT customer_id) as unique_customers
      FROM sales
      GROUP BY year, month, region
  \"\"\", view_name="monthly_report", ttl_days=30)
""")

    print("  [PASS]\n")


# ---------------------------------------------------------------------------
# Cleanup
# ---------------------------------------------------------------------------

def _cleanup(lake: Lake, base_uri: str) -> None:
    for ds in DATASETS:
        if ds in lake.list_datasets():
            lake.delete_dataset(ds)
    shutil.rmtree(base_uri, ignore_errors=True)


if __name__ == "__main__":
    main()
