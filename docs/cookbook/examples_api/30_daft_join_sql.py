#!/usr/bin/env python3
"""API-30 — Daft join 跨表关联 + SQL 高级查询

业务场景: 数据分析师需要将订单数据与区域维度表关联，
         然后用 SQL 做窗口函数分析（同品类排名）和 CTE 递进筛选。
数据源: datas/transactions/sales_2024.csv (1000 条订单) + 内生区域维度表
流程: 摄取订单 + 创建维度表 → join 关联 → SQL 窗口函数 → SQL CTE
"""

from __future__ import annotations
import os

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from conftest import ArrowLakeClient

import pyarrow as pa
import pyarrow.csv as pcsv
import daft
import lance

DATAS_DIR = Path(__file__).resolve().parent.parent / "datas"
BASE_URL = os.environ.get("ARROW_LAKE_BASE_URL", "http://localhost:8000")
API_KEY = os.environ.get("ARROW_LAKE_API_KEY", "dev-api-key-for-local-testing-only")

DS_ORDERS = "cookbook-30-orders"
DS_REGIONS = "cookbook-30-regions"


def main() -> None:
    print("=" * 60)
    print("API-30  Daft join 跨表关联 + SQL 高级查询")
    print("=" * 60)

    # ── Phase 1: 数据准备 ──
    print("\n── Phase 1: 数据摄取 ──")

    csv_path = DATAS_DIR / "transactions" / "sales_2024.csv"
    assert csv_path.exists(), f"数据文件不存在: {csv_path}"

    orders_table = pcsv.read_csv(str(csv_path))

    # 提取去重区域维度表
    regions_set = set()
    for i in range(orders_table.num_rows):
        regions_set.add(orders_table.column("region")[i].as_py())
    regions_table = pa.table({
        "region": sorted(regions_set),
        "region_id": list(range(1, len(regions_set) + 1)),
        "tier": ["tier-1" if i < 3 else "tier-2" for i in range(len(regions_set))],
    })

    tmp = Path("/tmp")
    lance.write_dataset(orders_table, str(tmp / f"{DS_ORDERS}.lance"), mode="overwrite")
    lance.write_dataset(regions_table, str(tmp / f"{DS_REGIONS}.lance"), mode="overwrite")
    print(f"  订单表: {orders_table.num_rows} 行, {orders_table.num_columns} 列")
    print(f"  区域维度表: {regions_table.num_rows} 行 ({', '.join(regions_table.column('region').to_pylist())})")

    from arrow_lake.query.daft_api import DaftQueryEngine

    engine = DaftQueryEngine(base_uri=str(tmp))
    orders = engine.load(DS_ORDERS)
    regions = engine.load(DS_REGIONS)

    # ── Phase 2: join 跨表关联 ──
    print("\n── Phase 2: join 跨表关联 ──")

    print("\n  STEP 1: inner join 订单 × 区域维度表")
    joined = (orders.select("order_id", "category", "amount", "region")
              .join(regions.select("region", "region_id", "tier"),
                    on="region", how="inner")
              .collect())
    print(f"  结果: {joined.num_rows} 行 × {joined.num_columns} 列")
    for i in range(min(3, joined.num_rows)):
        print(f"    {joined.column('order_id')[i].as_py():12s}  "
              f"cat={joined.column('category')[i].as_py():12s}  "
              f"amt={joined.column('amount')[i].as_py():>10,.2f}  "
              f"region={joined.column('region')[i].as_py():14s}  "
              f"id={joined.column('region_id')[i].as_py()}  "
              f"tier={joined.column('tier')[i].as_py()}")
    assert joined.num_rows == orders_table.num_rows, "join 行数应与订单表一致"
    assert "region_id" in joined.column_names
    assert "tier" in joined.column_names
    print("  ✓ inner join 正确关联，新增 region_id + tier 列")

    print("\n  STEP 2: left join 验证（订单 left join 区域）")
    lj = orders.select("order_id", "region").join(
        regions.select("region", "tier"), on="region", how="left"
    ).collect()
    print(f"  结果: {lj.num_rows} 行 (与订单表一致)")
    assert lj.num_rows == orders_table.num_rows
    print("  ✓ left join 行数不变")

    # ── Phase 3: SQL 窗口函数 ──
    print("\n── Phase 3: SQL 窗口函数分析 ──")

    print("\n  STEP 3: 同品类销售额排名 (RANK)")
    ranked = orders.select("order_id", "category", "amount").sql(
        "SELECT order_id, category, amount, "
        "RANK() OVER (PARTITION BY category ORDER BY amount DESC) as rank "
        "FROM self"
    ).collect()
    print(f"  结果: {ranked.num_rows} 行 × {ranked.num_columns} 列")
    # 找到每个品类的 top 1
    cat_tops: dict[str, str] = {}
    for i in range(ranked.num_rows):
        rk = ranked.column("rank")[i].as_py()
        cat = ranked.column("category")[i].as_py()
        if rk == 1 and cat not in cat_tops:
            cat_tops[cat] = f"{ranked.column('amount')[i].as_py():,.2f}"
    print(f"  各品类 Top 1 订单 ({len(cat_tops)} 个品类):")
    for cat, amt in sorted(cat_tops.items()):
        print(f"    {cat:16s}  {amt}")
    assert ranked.num_rows == orders_table.num_rows
    assert "rank" in ranked.column_names
    print("  ✓ RANK() OVER 窗口函数正常")

    print("\n  STEP 4: 同品类前 3 名 (CTE + ROW_NUMBER)")
    top3_sql = orders.select("order_id", "category", "amount").sql("""
        WITH ranked AS (
            SELECT order_id, category, amount,
                   ROW_NUMBER() OVER (PARTITION BY category ORDER BY amount DESC) as rn
            FROM self
        )
        SELECT * FROM ranked WHERE rn <= 3 ORDER BY category, rn
    """).collect()
    print(f"  各品类 Top 3 (共 {top3_sql.num_rows} 行):")
    for i in range(min(12, top3_sql.num_rows)):
        cat = top3_sql.column("category")[i].as_py()
        amt = top3_sql.column("amount")[i].as_py()
        rn = top3_sql.column("rn")[i].as_py()
        print(f"    {cat:16s}  #{rn}  {amt:>10,.2f}")
    print("  ✓ CTE + ROW_NUMBER() 正常")

    # ── Phase 4: SQL CTE 递进筛选 ──
    print("\n── Phase 4: SQL CTE 递进筛选 ──")

    print("\n  STEP 5: CTE — 高价值订单的品类分析")
    cte_result = orders.select("order_id", "category", "amount", "region").sql("""
        WITH high_value AS (
            SELECT * FROM self WHERE amount > 1500
        ),
        by_category AS (
            SELECT category, COUNT(*) as order_count, SUM(amount) as total_amount
            FROM high_value
            GROUP BY category
        )
        SELECT * FROM by_category ORDER BY total_amount DESC
    """).collect()
    print(f"  结果: {cte_result.num_rows} 个品类有高价值订单 (>1500)")
    for i in range(cte_result.num_rows):
        cat = cte_result.column("category")[i].as_py()
        cnt = cte_result.column("order_count")[i].as_py()
        total = cte_result.column("total_amount")[i].as_py()
        print(f"    {cat:16s}  {cnt} 笔  总额 {total:>12,.2f}")
    print("  ✓ CTE 递进筛选正常")

    print("\n  STEP 6: CTE — 多层嵌套（高价值 → Tier-1 区域）")
    multi_cte = orders.select("order_id", "category", "amount", "region").sql("""
        WITH high_value AS (
            SELECT * FROM self WHERE amount > 1000
        ),
        top_regions AS (
            SELECT region, COUNT(*) as cnt, AVG(amount) as avg_amount
            FROM high_value
            GROUP BY region
            HAVING cnt > 10
        )
        SELECT * FROM top_regions ORDER BY avg_amount DESC
    """).collect()
    print(f"  结果: {multi_cte.num_rows} 个区域满足条件")
    for i in range(multi_cte.num_rows):
        reg = multi_cte.column("region")[i].as_py()
        cnt = multi_cte.column("cnt")[i].as_py()
        avg = multi_cte.column("avg_amount")[i].as_py()
        print(f"    {reg:16s}  {cnt} 笔  均价 {avg:>10,.2f}")
    print("  ✓ 多层 CTE 正常")

    # 清理
    import shutil
    shutil.rmtree(str(tmp / f"{DS_ORDERS}.lance"), ignore_errors=True)
    shutil.rmtree(str(tmp / f"{DS_REGIONS}.lance"), ignore_errors=True)

    print("\n" + "=" * 60)
    print("API-30  ALL PASSED")
    print("=" * 60)


if __name__ == "__main__":
    main()
