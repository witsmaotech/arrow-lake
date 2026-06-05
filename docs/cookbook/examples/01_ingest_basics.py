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
    print(DATAS_DIR)
    print("=" * 60)

    base = Path(args.base_uri)
    if base.exists():
        shutil.rmtree(base)

    try:
        lake = Lake(base_uri=args.base_uri)
    except Exception as e:
        print(f"Failed to initialize Lake: {e}")
        sys.exit(1)
    print(f"Arrow Lake v{lake.version()}\n")

    # 清理 MinIO / 后端中的残留数据集
    _DATASETS = ["transactions", "knowledge", "papers_zh"]
    for ds in _DATASETS:
        try:
            lake.delete_dataset(ds)
        except Exception:
            pass

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

    # --- STEP 4: 验证本示例创建的数据集 ---
    print("STEP 4: 验证数据集")
    datasets = lake.list_datasets()
    missing = [d for d in _DATASETS if d not in datasets]
    if missing:
        print(f"  [FAIL] Missing: {missing}")
        return
    for d in _DATASETS:
        print(f"  - {d}")
    print(f"  [PASS]\n")

    # --- STEP 5: 查看数据集详情 ---
    print("STEP 5: 数据集详情")
    catalog = lake.catalog()
    for name in _DATASETS:
        if name in catalog.datasets:
            ds = catalog.datasets[name]
            print(f"  {name}: {ds.num_rows} 行, {ds.version} 版")
    print("  [PASS]\n")

    # 清理
    if not no_cleanup:
        for ds in _DATASETS:
            try:
                lake.delete_dataset(ds)
            except Exception:
                pass
        lake.shutdown()
        shutil.rmtree(base, ignore_errors=True)
        print("(已清理临时数据)")

    print("=" * 60)
    print("01 完成")
    print("=" * 60)


if __name__ == "__main__":
    main()
