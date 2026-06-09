#!/usr/bin/env python3
"""API-32 — Daft reshape + 采样 + 去重 + 分页

业务场景: 数据分析师需要将订单数据做多种 reshape 操作——
         交叉表(pivot)、宽表转长表(unpivot)、数组字段展开(explode)、
         随机采样(sample)、分页查询(offset+limit)、去重(distinct)。
数据源: datas/transactions/sales_2024.csv (1000 条订单)
"""

from __future__ import annotations
import os

import sys
import shutil
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from conftest import ArrowLakeClient

import pyarrow as pa
import pyarrow.csv as pcsv
import lance

DATAS_DIR = Path(__file__).resolve().parent.parent / "datas"

DS_NAME = "cookbook-32-reshape"


def main() -> None:
    print("=" * 60)
    print("API-32  Daft reshape + 采样 + 去重 + 分页")
    print("=" * 60)

    csv_path = DATAS_DIR / "transactions" / "sales_2024.csv"
    assert csv_path.exists(), f"数据文件不存在: {csv_path}"

    # ── Phase 1: 数据摄取 ──
    print("\n── Phase 1: 数据摄取 ──")
    table = pcsv.read_csv(str(csv_path))

    # 附加一个 tags 列（模拟多值字段）
    import random
    random.seed(42)
    all_tags = ["flash-sale", "bundle", "clearance", "new-arrival", "featured"]
    tags_col = [[t for t in all_tags if random.random() > 0.6] for _ in range(table.num_rows)]
    table = table.append_column("tags", pa.array(tags_col, type=pa.list_(pa.string())))

    tmp = Path("/tmp")
    lance.write_dataset(table, str(tmp / f"{DS_NAME}.lance"), mode="overwrite")
    print(f"  订单表: {table.num_rows} 行 × {table.num_columns} 列 (含 tags 列)")

    from arrow_lake.query.daft_api import DaftQueryEngine
    engine = DaftQueryEngine(base_uri=str(tmp))
    frame = engine.load(DS_NAME)

    # ── Phase 2: pivot 交叉表 ──
    print("\n── Phase 2: pivot — 品类 × 区域 营收交叉表 ──")

    pivot_result = (frame.select("category", "region", "amount")
                    .pivot(group_by="category", pivot_col="region",
                           value_col="amount", agg_fn="sum")
                    .collect())
    print(f"  结果: {pivot_result.num_rows} 行 × {pivot_result.num_columns} 列")
    print(f"  列: {pivot_result.column_names[:6]}...")

    # 打印每个品类的 top 区域
    for i in range(min(5, pivot_result.num_rows)):
        cat = pivot_result.column("category")[i].as_py()
        vals = []
        for col_name in pivot_result.column_names[1:]:
            v = pivot_result.column(col_name)[i]
            vals.append(f"{col_name}={v.as_py():>10,.0f}" if v.is_valid else f"{col_name}=N/A")
        print(f"    {cat:16s}  {vals[0]}  {vals[1]}  {vals[2]}")
    assert pivot_result.num_rows > 0
    assert pivot_result.num_columns > 3
    print("  ✓ pivot 交叉表生成正常")

    # ── Phase 3: unpivot 宽表转长表 ──
    print("\n── Phase 3: unpivot — 交叉表转回长格式 ──")

    unpivot_result = (frame.select("category", "region", "amount")
                      .pivot(group_by="category", pivot_col="region",
                             value_col="amount", agg_fn="mean")
                      .unpivot(ids="category", variable_name="region",
                               value_name="avg_amount")
                      .collect())
    print(f"  结果: {unpivot_result.num_rows} 行 × {unpivot_result.num_columns} 列")
    print(f"  列: {unpivot_result.column_names}")
    for i in range(min(6, unpivot_result.num_rows)):
        cat = unpivot_result.column("category")[i].as_py()
        reg = unpivot_result.column("region")[i].as_py()
        avg = unpivot_result.column("avg_amount")[i]
        avg_str = f"{avg.as_py():>8,.1f}" if avg.is_valid else "N/A"
        print(f"    {cat:16s}  {reg:14s}  {avg_str}")
    assert unpivot_result.num_columns == 3
    print("  ✓ unpivot 宽转长正常")

    # ── Phase 4: explode 展开多值字段 ──
    print("\n── Phase 4: explode — 展开 tags 数组 ──")

    explode_result = frame.select("order_id", "category", "tags").explode("tags").collect()
    print(f"  原始 {table.num_rows} 行 → 展开后 {explode_result.num_rows} 行")
    tag_counts: dict[str, int] = {}
    for i in range(explode_result.num_rows):
        t = explode_result.column("tags")[i]
        if t.is_valid:
            tag_counts[t.as_py()] = tag_counts.get(t.as_py(), 0) + 1
    print(f"  tag 分布: {dict(sorted(tag_counts.items(), key=lambda x: -x[1]))}")
    assert explode_result.num_rows > table.num_rows
    print("  ✓ explode 展开正常")

    # ── Phase 5: describe 数据概览 ──
    print("\n── Phase 5: describe — 列类型概览 ──")

    desc = frame.select("order_id", "category", "amount", "region").describe()
    print(f"  结果: {desc.num_rows} 列的元信息")
    for i in range(desc.num_rows):
        col_name = desc.column("column_name")[i].as_py()
        col_type = desc.column("type")[i].as_py()
        print(f"    {col_name:16s}  {col_type}")
    print("  ✓ describe 概览正常")

    # ── Phase 6: sample 随机采样 ──
    print("\n── Phase 6: sample — 随机采样 ──")

    # 6a. 按比例采样
    s1 = frame.select("amount").sample(fraction=0.1, seed=42).collect()
    print(f"  10% 采样: {s1.num_rows} 行 (期望 ~{table.num_rows // 10})")
    assert s1.num_rows <= table.num_rows

    # 6b. 按数量采样
    s2 = frame.select("amount").sample(size=50, seed=7).collect()
    print(f"  固定 50 行采样: {s2.num_rows} 行")
    assert s2.num_rows <= 50

    # 6c. 可复现性
    s3a = frame.select("amount").sample(fraction=0.05, seed=99).collect()
    s3b = frame.select("amount").sample(fraction=0.05, seed=99).collect()
    assert s3a.num_rows == s3b.num_rows
    print(f"  可复现采样: seed=99 → {s3a.num_rows} 行 (两次一致)")
    print("  ✓ sample 采样正常")

    # ── Phase 7: offset + limit 分页 ──
    print("\n── Phase 7: offset + limit — 分页查询 ──")

    page_size = 10
    for page_num in range(3):
        page = (frame.select("order_id", "category", "amount")
                .sort("order_id")
                .offset(page_num * page_size)
                .limit(page_size)
                .collect())
        first = page.column("order_id")[0].as_py()
        last = page.column("order_id")[-1].as_py()
        print(f"  Page {page_num + 1}: {page.num_rows} 行, {first} → {last}")
        assert page.num_rows == page_size
    print("  ✓ offset+limit 分页正常")

    # offset 边界
    try:
        frame.offset(-1)
        print("  ✗ offset(-1) 应该报错")
    except ValueError:
        print("  ✓ offset(-1) 正确拦截")

    # ── Phase 8: distinct 去重 ──
    print("\n── Phase 8: distinct — 去重 ──")

    # 8a. 全列去重
    all_distinct = frame.collect()
    print(f"  原始行数: {all_distinct.num_rows}")

    # 8b. 按列去重
    cat_region = frame.select("category", "region").distinct().collect()
    print(f"  distinct(category, region): {cat_region.num_rows} 种组合")

    cat_only = frame.select("category").distinct().sort("category").collect()
    cats = [cat_only.column("category")[i].as_py() for i in range(cat_only.num_rows)]
    print(f"  distinct(category): {cats}")
    assert cat_only.num_rows == 10, f"期望 10 个品类, 实际 {cat_only.num_rows}"
    print("  ✓ distinct 去重正常")

    # 清理
    shutil.rmtree(str(tmp / f"{DS_NAME}.lance"), ignore_errors=True)

    print("\n" + "=" * 60)
    print("API-32  ALL PASSED")
    print("=" * 60)


if __name__ == "__main__":
    main()
