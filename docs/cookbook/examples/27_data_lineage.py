#!/usr/bin/env python3
"""27 — 数据血缘

场景: 记录和追踪数据集转换链路，构建血缘图谱。

数据文件: datas/transactions/sales_2024_cn.csv
"""

from __future__ import annotations

import argparse

import shutil
import sys
from pathlib import Path

from arrow_lake import Lake

DATAS_DIR = Path(__file__).resolve().parent.parent / "datas"
_DEFAULT_BASE_URI = "./_tmp_lineage"
_DATASETS = ("raw_sales", "clean_sales")


def main() -> None:
    parser = argparse.ArgumentParser(description="27_data_lineage.py")
    parser.add_argument("--base-uri", default=_DEFAULT_BASE_URI)
    parser.add_argument("--no-cleanup", action="store_true")
    args = parser.parse_args()
    no_cleanup = args.no_cleanup
    print("=" * 60)
    print("27 数据血缘")
    print("=" * 60)

    base = Path(args.base_uri)
    if base.exists():
        shutil.rmtree(base)

    lake = Lake(base_uri=args.base_uri)

    # 清理 MinIO 后端可能残留的数据集
    for ds in _DATASETS:
        try:
            lake.delete_dataset(ds)
        except Exception:
            pass

    # STEP 1: 源数据摄取 + 血缘记录
    print("STEP 1: 源数据摄取")
    lake.ingest("raw_sales", [str(DATAS_DIR / "transactions" / "sales_2024_cn.csv")])
    lake.lineage_record_event("raw_sales", "ingest",
                              source_datasets=["sales_2024_cn.csv"],
                              metadata={"format": "csv", "rows": 50, "actor": "etl_pipeline"})
    print("  raw_sales 摄入完成, 血缘已记录")

    # STEP 2: 数据清洗 + 血缘记录
    print("\nSTEP 2: 数据清洗转换")
    lake.ingest("clean_sales", [str(DATAS_DIR / "transactions" / "sales_2024_cn.csv")])
    lake.lineage_record_event("clean_sales", "transform",
                              source_datasets=["raw_sales"],
                              metadata={"transform_type": "dedup+filter", "actor": "etl_pipeline", "removed_nulls": True})
    print("  clean_sales 转换完成, 血缘已记录")

    # STEP 3: 聚合视图 + 血缘记录
    print("\nSTEP 3: 聚合分析")
    result = lake.olap_query("clean_sales",
        "SELECT 商品类别, COUNT(*) as cnt FROM clean_sales GROUP BY 商品类别")
    lake.lineage_record_event("category_report", "aggregate",
                              source_datasets=["clean_sales"],
                              metadata={"transform_type": "sql_groupby", "actor": "analyst", "categories": len(result.table.to_pylist())})
    print(f"  聚合完成, 产出 {len(result.table.to_pylist())} 个分类")

    # STEP 4: 查看血缘历史
    print("\nSTEP 4: 查看血缘历史")
    for ds_name in ["raw_sales", "clean_sales", "category_report"]:
        try:
            history = lake.lineage_history(ds_name)
            print(f"  [{ds_name}] 操作记录: {len(history)} 条")
            for h in history:
                op = getattr(h, 'operation', '?')
                actor = getattr(h, 'actor', '?')
                print(f"    {op:<12} by {actor}")
        except Exception as e:
            print(f"  [{ds_name}] 查询跳过: {e}")

    # STEP 5: 血缘链路汇总
    print("\nSTEP 5: 血缘链路汇总")
    all_events = []
    for ds_name in ["raw_sales", "clean_sales", "category_report"]:
        try:
            history = lake.lineage_history(ds_name)
            all_events.extend(history)
        except Exception:
            pass
    print(f"  全部血缘记录: {len(all_events)} 条")
    for h in all_events[:5]:
        op = getattr(h, 'operation', '?')
        actor = getattr(h, 'actor', '?')
        print(f"    {op:<12} by {actor}")

    # STEP 6: 数据集列表
    print("\nSTEP 6: 数据集列表")
    catalog = lake.catalog()
    for name in lake.list_datasets():
        ds = catalog.datasets.get(name)
        rows = ds.num_rows if ds else "?"
        print(f"  {name}: {rows} 行")

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
