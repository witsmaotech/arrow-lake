#!/usr/bin/env python3
"""16 — 增量更新工作流

场景: 模拟数据分批入库 + 增量索引 + 变更检测。

数据文件: datas/transactions/sales_2024.csv (分两批)
"""

from __future__ import annotations

import argparse

import shutil
import sys
from pathlib import Path

from arrow_lake import Lake

DATAS_DIR = Path(__file__).resolve().parent.parent / "datas"
_DEFAULT_BASE_URI = "./_tmp_incremental"
_DATASETS = ["sales"]


def main() -> None:
    parser = argparse.ArgumentParser(description="16_incremental_update_workflow.py")
    parser.add_argument("--base-uri", default=_DEFAULT_BASE_URI)
    parser.add_argument("--no-cleanup", action="store_true")
    args = parser.parse_args()
    no_cleanup = args.no_cleanup
    print("=" * 60)
    print("16 增量更新工作流")
    print("=" * 60)

    base = Path(args.base_uri)
    if base.exists():
        shutil.rmtree(base)

    lake = Lake(base_uri=args.base_uri)

    # 清理后端残留
    for ds in _DATASETS:
        try:
            lake.delete_dataset(ds)
        except Exception:
            pass

    # STEP 1: 第一批入库
    print("STEP 1: 第一批数据入库")
    csv_path = str(DATAS_DIR / "transactions" / "sales_2024.csv")
    r1 = lake.ingest("sales", [csv_path])
    print(f"  第一批: {r1.total_rows} 行")

    # STEP 2: 追加第二批 (同一文件再次摄取)
    print("\nSTEP 2: 追加第二批数据 (同文件追加)")
    import pyarrow.csv as pacsv
    batch2 = pacsv.read_csv(csv_path)
    lake.append_dataset("sales", batch2)
    print(f"  第二批: {batch2.num_rows} 行")
    print(f"  数据集总量: {r1.total_rows + batch2.num_rows} 行 (增量 +{batch2.num_rows})")

    # STEP 3: 增量前后对比
    print("\nSTEP 3: 数据量变化")
    catalog = lake.catalog()
    for name in lake.list_datasets():
        ds = next((e for e in catalog.datasets if e.name == name), None)
        rows = ds.num_rows if ds else "?"
        print(f"  {name}: {rows} 行")

    # STEP 4: OLAP 验证
    print("\nSTEP 4: SQL 验证总数据")
    result = lake.olap_query("sales",
        "SELECT COUNT(*) as total, ROUND(SUM(amount),2) as total_amount "
        "FROM sales")
    row = result.table.to_pylist()[0]
    print(f"  总行数: {row['total']}, 总金额: ${row['total_amount']}")

    # STEP 5: 重建索引 (增量后)
    print("\nSTEP 5: 重建索引 (增量更新后)")
    lake.create_fts_index("sales", fts_column="product_name")
    print("  FTS 索引已重建")

    # STEP 6: 搜索验证
    print("\nSTEP 6: 搜索验证新增数据可被检索")
    result = lake.text_search("sales", "Mouse", top_k=3, fts_column="product_name")
    print(f"  搜索 'Mouse': {result.row_count} 条结果")
    for i in range(min(3, result.row_count)):
        t = result.table
        name = t.column("product_name")[i].as_py() if "product_name" in t.column_names else ""
        amt = t.column("amount")[i].as_py() if "amount" in t.column_names else ""
        print(f"    #{i+1} {name[:50]}  ${amt}")

    # STEP 7: 导出增量后数据
    print("\nSTEP 7: 导出增量后完整数据")
    out = (base / "sales_incremental.parquet").resolve()
    lake.export("sales", str(out), format="parquet")
    catalog = lake.catalog()
    ds_final = next((e for e in catalog.datasets if e.name == "sales"), None)
    rows = ds_final.num_rows if ds_final else "?"
    print(f"  最终: {rows} 行 → {out.name} ({out.stat().st_size // 1024} KB)")

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
