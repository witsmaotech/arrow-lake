#!/usr/bin/env python3
"""01 — 数据摄取入门

演示 CSV、JSONL、中文 CSV 三种格式的摄取，
以及 list_datasets / catalog 数据集查看。

数据文件: datas/transactions/sales_2024.csv, datas/kb/knowledge.jsonl, datas/papers/metadata_zh.csv
"""

from __future__ import annotations

import argparse

import shutil
import sys
from pathlib import Path

from arrow_lake import Lake

DATAS_DIR = Path(__file__).resolve().parent.parent / "datas"
_DEFAULT_BASE_URI = "./_tmp_ingest_basics"


def main() -> None:
    parser = argparse.ArgumentParser(description="01_ingest_basics.py")
    parser.add_argument("--base-uri", default=_DEFAULT_BASE_URI)
    parser.add_argument("--no-cleanup", action="store_true")
    args = parser.parse_args()
    no_cleanup = args.no_cleanup
    print("=" * 60)
    print("01 数据摄取入门")
    print("=" * 60)

    # 清理旧数据
    base = Path(args.base_uri)
    if base.exists():
        shutil.rmtree(base)

    lake = Lake(base_uri=args.base_uri)
    print(f"Arrow Lake v{lake.version()}\n")

    # --- STEP 1: 摄取 CSV ---
    print("STEP 1: 摄取 CSV (英文交易记录)")
    csv_path = str(DATAS_DIR / "transactions" / "sales_2024.csv")
    report = lake.ingest("transactions", [csv_path])
    print(f"  数据集: transactions")
    print(f"  摄入: {report.total_rows} 行, {report.total_files} 文件")
    print("  [PASS]\n")

    # --- STEP 2: 摄取 JSONL ---
    print("STEP 2: 摄取 JSONL (英文知识库)")
    jsonl_path = str(DATAS_DIR / "kb" / "knowledge.jsonl")
    report = lake.ingest("knowledge", [jsonl_path])
    print(f"  数据集: knowledge")
    print(f"  摄入: {report.total_rows} 行, {report.total_files} 文件")
    print("  [PASS]\n")

    # --- STEP 3: 摄取中文 CSV ---
    print("STEP 3: 摄取 CSV (中文论文元数据)")
    zh_csv = str(DATAS_DIR / "papers" / "metadata_zh.csv")
    report = lake.ingest("papers_zh", [zh_csv])
    print(f"  数据集: papers_zh")
    print(f"  摄入: {report.total_rows} 行, {report.total_files} 文件")
    print("  [PASS]\n")

    # --- STEP 4: 列出数据集 ---
    print("STEP 4: 列出全部数据集")
    datasets = lake.list_datasets()
    for i, name in enumerate(datasets, 1):
        print(f"  {i}. {name}")
    if len(datasets) != 3:
        print(f"  [FAIL] Expected 3 items, got {len(datasets)}")
        return
    print(f"  共 {len(datasets)} 个数据集")
    print("  [PASS]\n")

    # --- STEP 5: 查看数据集详情 ---
    print("STEP 5: 数据集详情")
    for name in datasets:
        catalog = lake.catalog()
        if name in catalog.datasets:
            ds = catalog.datasets[name]
            print(f"  {name}: {ds.num_rows} 行, {ds.version} 版")
    print("  [PASS]\n")

    # 清理
    if not no_cleanup:
        lake.shutdown()
        shutil.rmtree(base, ignore_errors=True)
        print("(已清理临时数据)")

    print("=" * 60)
    print("01 完成")
    print("=" * 60)


if __name__ == "__main__":
    main()
