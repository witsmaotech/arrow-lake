#!/usr/bin/env python3
"""API-31 — Daft 多数据源 Pipeline + 配置调优

业务场景: 数据工程师需要构建一个多步管道——从订单表中提取各品类 Top 客户，
         再与维度表 join 生成最终报表，并通过 DaftConfig 调优执行计划。
数据源: datas/transactions/sales_2024.csv (1000 条订单) + 内生维度表
流程: 配置 DaftConfig → 摄取多表 → 分组排名 → join → 输出报表
"""

from __future__ import annotations

import sys
import shutil
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from conftest import ArrowLakeClient

import pyarrow as pa
import pyarrow.csv as pcsv
import daft
import lance

DATAS_DIR = Path(__file__).resolve().parent.parent / "datas"
BASE_URL = "http://localhost:8000"
API_KEY = "dev-api-key-for-local-testing-only"

DS_ORDERS = "cookbook-31-orders"
DS_PRODUCTS = "cookbook-31-products"


def main() -> None:
    print("=" * 60)
    print("API-31  Daft 多数据源 Pipeline + 配置调优")
    print("=" * 60)

    # ── Phase 1: 数据准备 ──
    print("\n── Phase 1: 多表数据摄取 ──")

    csv_path = DATAS_DIR / "transactions" / "sales_2024.csv"
    assert csv_path.exists(), f"数据文件不存在: {csv_path}"

    orders_table = pcsv.read_csv(str(csv_path))

    # 生成产品维度表
    products_set = set()
    for i in range(orders_table.num_rows):
        products_set.add(orders_table.column("product_name")[i].as_py())
    products_table = pa.table({
        "product_name": sorted(products_set),
        "product_id": list(range(1, len(products_set) + 1)),
        "margin_pct": [round(15 + (hash(p) % 30), 1) for p in sorted(products_set)],
    })

    tmp = Path("/tmp")
    lance.write_dataset(orders_table, str(tmp / f"{DS_ORDERS}.lance"), mode="overwrite")
    lance.write_dataset(products_table, str(tmp / f"{DS_PRODUCTS}.lance"), mode="overwrite")
    print(f"  订单表: {orders_table.num_rows} 行")
    print(f"  产品维度表: {products_table.num_rows} 行 (利润率 15%-45%)")

    # ── Phase 2: DaftConfig 性能调优 ──
    print("\n── Phase 2: DaftConfig 配置调优 ──")

    from arrow_lake.config.infra import DaftConfig
    from arrow_lake.query.daft_api import DaftQueryEngine

    cfg = DaftConfig(
        default_num_partitions=8,
        target_partition_max_memory_bytes=512 * 1024 * 1024,
        read_num_threads=4,
    )
    engine = DaftQueryEngine(base_uri=str(tmp), daft_config=cfg)
    print(f"  DaftConfig: partitions={cfg.default_num_partitions}, "
          f"mem_per_part={cfg.target_partition_max_memory_bytes // 1024 // 1024}MB, "
          f"read_threads={cfg.read_num_threads}")
    print("  ✓ DaftQueryEngine + DaftConfig 初始化成功（新旧版本兼容）")

    orders = engine.load(DS_ORDERS)
    products = engine.load(DS_PRODUCTS)

    # ── Phase 3: SQL 品类 Top 客户 ──
    print("\n── Phase 3: SQL 分组排名 ──")

    print("\n  STEP 1: 各品类消费 Top 3 客户 (CTE + ROW_NUMBER)")
    top_customers = orders.select("user_id", "category", "amount").sql("""
        WITH ranked AS (
            SELECT user_id, category, amount,
                   ROW_NUMBER() OVER (PARTITION BY category ORDER BY amount DESC) as rn
            FROM self
        )
        SELECT user_id, category, amount, rn FROM ranked WHERE rn <= 3
        ORDER BY category, rn
    """).collect()
    print(f"  结果: {top_customers.num_rows} 行 (10 品类 × 3 = 30)")
    for i in range(min(6, top_customers.num_rows)):
        uid = top_customers.column("user_id")[i].as_py()
        cat = top_customers.column("category")[i].as_py()
        amt = top_customers.column("amount")[i].as_py()
        rn = top_customers.column("rn")[i].as_py()
        print(f"    {uid:10s}  {cat:16s}  #{rn}  {amt:>10,.2f}")
    assert top_customers.num_rows == 30, f"期望 30 行, 实际 {top_customers.num_rows}"
    print("  ✓ 品类 Top 客户排名正确")

    # ── Phase 4: join 多表关联 ──
    print("\n── Phase 4: join 订单 × 产品维度表 ──")

    print("\n  STEP 2: 计算利润额 (订单 join 产品维度表)")
    order_products = (orders.select("order_id", "product_name", "amount")
                      .join(products.select("product_name", "margin_pct"),
                            on="product_name", how="inner")
                      .collect())
    print(f"  结果: {order_products.num_rows} 行 × {order_products.num_columns} 列")
    assert order_products.num_rows == orders_table.num_rows
    assert "margin_pct" in order_products.column_names
    # 计算总利润
    amounts = order_products.column("amount").to_pylist()
    margins = order_products.column("margin_pct").to_pylist()
    total_profit = sum(a * m / 100 for a, m in zip(amounts, margins))
    print(f"  总利润额: {total_profit:,.2f}")
    print("  ✓ join 利润率计算正常")

    # ── Phase 5: limit 分页查询 ──
    print("\n── Phase 5: limit 分页查询 ──")

    print("\n  STEP 3: 模拟分页 (page 1: limit 10)")
    page1 = orders.select("order_id", "category", "amount").sort("order_id").limit(10).collect()
    print(f"  Page 1: {page1.num_rows} 行")
    first_id = page1.column("order_id")[0].as_py()
    last_id = page1.column("order_id")[-1].as_py()
    print(f"    从 {first_id} 到 {last_id}")
    assert page1.num_rows == 10
    print("  ✓ limit 分页正常")

    # ── Phase 6: 完整 pipeline 报表 ──
    print("\n── Phase 6: 完整 Pipeline 报表 ──")

    print("\n  STEP 4: 生成品类汇总报表 (groupby + sort + limit)")
    report = (orders.select("category", "amount")
              .groupby("category").agg(
                  daft.col("amount").sum().alias("revenue"),
                  daft.col("amount").count().alias("orders"),
                  daft.col("amount").mean().alias("avg_ticket"),
              )
              .sort("revenue", desc=True)
              .collect())
    print(f"  品类营收排名 ({report.num_rows} 个品类):")
    for i in range(report.num_rows):
        cat = report.column("category")[i].as_py()
        rev = report.column("revenue")[i].as_py()
        cnt = report.column("orders")[i].as_py()
        avg = report.column("avg_ticket")[i].as_py()
        bar = "█" * int(rev / 5000)
        print(f"    {cat:16s}  {rev:>10,.0f}  ({cnt:3d}笔  均{avg:>6,.0f})  {bar}")
    print("  ✓ 品类汇总报表生成完成")

    # 清理
    shutil.rmtree(str(tmp / f"{DS_ORDERS}.lance"), ignore_errors=True)
    shutil.rmtree(str(tmp / f"{DS_PRODUCTS}.lance"), ignore_errors=True)

    print("\n" + "=" * 60)
    print("API-31  ALL PASSED")
    print("=" * 60)


if __name__ == "__main__":
    main()
