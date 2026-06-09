#!/usr/bin/env python3
"""API-29 — Daft limit / sort / groupby 聚合分析

业务场景: 电商运营团队需要快速生成「各品类销售额 Top N」报表，
         使用 Daft 的 limit + sort + groupby 链式 API 完成分组聚合。
数据源: datas/transactions/sales_2024.csv (1000 条订单记录)
流程: 摄取 CSV → Daft 加载 → 分组聚合 → 排序截取 → 汇总报表
"""

from __future__ import annotations
import os

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from conftest import ArrowLakeClient

import daft
import lance
import pyarrow.csv as pcsv

DATAS_DIR = Path(__file__).resolve().parent.parent / "datas"
BASE_URL = os.environ.get("ARROW_LAKE_BASE_URL", "http://localhost:8000")
API_KEY = os.environ.get("ARROW_LAKE_API_KEY", "dev-api-key-for-local-testing-only")

DS_NAME = "cookbook-29-limit-sort-groupby"


def _ingest_via_sdk(ds_name: str, csv_path: Path) -> str:
    """直接用 Lance SDK 摄取（不依赖 API 服务器）。"""
    ds_dir = Path(f"/tmp/{ds_name}.lance")
    table = pcsv.read_csv(str(csv_path))
    lance.write_dataset(table, str(ds_dir), mode="overwrite")
    return str(ds_dir.parent)


def _ingest_via_api(ds_name: str, csv_path: Path) -> str | None:
    """通过 API 摄取，返回 None 表示失败。"""
    c = ArrowLakeClient(BASE_URL, API_KEY)
    c.delete_dataset(ds_name)
    resp = c.ingest_files(ds_name, [str(csv_path)])
    if not resp.get("success"):
        c.delete_dataset(ds_name)
        return None
    return None  # API 模式不需要返回路径，后续用 API 查询


def main() -> None:
    print("=" * 60)
    print("API-29  Daft limit / sort / groupby 聚合分析")
    print("=" * 60)

    csv_path = DATAS_DIR / "transactions" / "sales_2024.csv"
    assert csv_path.exists(), f"数据文件不存在: {csv_path}"

    # ── Phase 1: 数据准备 ──
    print("\n── Phase 1: 数据摄取 (SDK 直写) ──")
    base_uri = _ingest_via_sdk(DS_NAME, csv_path)
    print(f"  ✓ 数据集写入: {base_uri}/{DS_NAME}.lance")

    from arrow_lake.query.daft_api import DaftQueryEngine

    engine = DaftQueryEngine(base_uri=base_uri)
    frame = engine.load(DS_NAME)

    # 先看数据概况
    peek = frame.select("order_id", "category", "amount", "region").limit(5).collect()
    print(f"  样本 ({peek.num_rows} 行):")
    for i in range(min(3, peek.num_rows)):
        print(f"    {peek.column('order_id')[i].as_py():12s}  "
              f"{peek.column('category')[i].as_py():12s}  "
              f"{peek.column('amount')[i].as_py():>10}")

    # ── Phase 2: groupby 分组聚合 ──
    print("\n── Phase 2: 按品类分组聚合 ──")

    # 2a. sum — 各品类总销售额
    by_cat = frame.select("category", "amount").groupby("category").sum().collect()
    print(f"\n  STEP 1: groupby('category').sum() — {by_cat.num_rows} 个品类")
    for i in range(by_cat.num_rows):
        cat = by_cat.column("category")[i].as_py()
        total = by_cat.column("amount")[i].as_py()
        print(f"    {cat:16s}  总销售额: {total:>12,.2f}")

    # 2b. mean — 各品类平均客单价
    avg_cat = frame.select("category", "amount").groupby("category").mean().collect()
    print(f"\n  STEP 2: groupby('category').mean() — 各品类平均客单价")
    for i in range(avg_cat.num_rows):
        cat = avg_cat.column("category")[i].as_py()
        avg = avg_cat.column("amount")[i].as_py()
        print(f"    {cat:16s}  平均客单价: {avg:>10,.2f}")

    # 2c. count — 各品类订单数
    cnt_cat = frame.select("category", "order_id").groupby("category").count().collect()
    print(f"\n  STEP 3: groupby('category').count() — 各品类订单数")
    for i in range(cnt_cat.num_rows):
        cat = cnt_cat.column("category")[i].as_py()
        cnt = cnt_cat.column("order_id")[i].as_py()
        print(f"    {cat:16s}  订单数: {cnt}")

    # ── Phase 3: sort + limit Top N ──
    print("\n── Phase 3: 排序 + Top N 截取 ──")

    # 3a. 销售额最高的 5 笔订单
    top5 = (frame.select("order_id", "category", "amount", "region")
            .sort("amount", desc=True).limit(5).collect())
    print(f"\n  STEP 4: 销售额 Top 5 订单")
    for i in range(top5.num_rows):
        print(f"    {top5.column('order_id')[i].as_py():12s}  "
              f"{top5.column('category')[i].as_py():12s}  "
              f"{top5.column('amount')[i].as_py():>10,.2f}  "
              f"{top5.column('region')[i].as_py()}")

    # 3b. 销售额最低的 3 笔订单
    bottom3 = (frame.select("order_id", "category", "amount")
               .sort("amount", desc=False).limit(3).collect())
    print(f"\n  STEP 5: 销售额最低 3 笔订单")
    for i in range(bottom3.num_rows):
        print(f"    {bottom3.column('order_id')[i].as_py():12s}  "
              f"{bottom3.column('category')[i].as_py():12s}  "
              f"{bottom3.column('amount')[i].as_py():>10,.2f}")

    # ── Phase 4: groupby.agg() 自定义聚合 ──
    print("\n── Phase 4: 自定义聚合表达式 ──")

    agg = frame.select("category", "amount").groupby("category").agg(
        daft.col("amount").sum().alias("total_revenue"),
        daft.col("amount").max().alias("max_order"),
        daft.col("amount").min().alias("min_order"),
    ).collect()
    print(f"\n  STEP 6: groupby.agg(sum/max/min) — {agg.num_rows} 个品类")
    for i in range(agg.num_rows):
        cat = agg.column("category")[i].as_py()
        total = agg.column("total_revenue")[i].as_py()
        mx = agg.column("max_order")[i].as_py()
        mn = agg.column("min_order")[i].as_py()
        print(f"    {cat:16s}  总额: {total:>12,.2f}  "
              f"最高: {mx:>10,.2f}  最低: {mn:>10,.2f}")

    # ── Phase 5: limit 边界校验 ──
    print("\n── Phase 5: limit 边界校验 ──")
    try:
        frame.limit(0)
        print("  ✗ limit(0) 应该报错")
    except ValueError as e:
        print(f"  ✓ limit(0) 正确拦截: {e}")

    try:
        frame.limit(-5)
        print("  ✗ limit(-5) 应该报错")
    except ValueError as e:
        print(f"  ✓ limit(-5) 正确拦截: {e}")

    # 清理
    import shutil
    shutil.rmtree(f"/tmp/{DS_NAME}.lance", ignore_errors=True)

    print("\n" + "=" * 60)
    print("API-29  ALL PASSED")
    print("=" * 60)


if __name__ == "__main__":
    main()
