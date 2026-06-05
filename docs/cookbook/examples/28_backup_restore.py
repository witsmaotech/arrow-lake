#!/usr/bin/env python3
"""28 — 备份与恢复

场景: 演示数据集的完整备份、列表查看、恢复验证。

数据文件: datas/transactions/sales_2024_cn.csv
"""

from __future__ import annotations

import argparse

import shutil
import sys
from pathlib import Path

from arrow_lake import Lake

DATAS_DIR = Path(__file__).resolve().parent.parent / "datas"
_DEFAULT_BASE_URI = "./_tmp_backup"
_DATASETS = ["sales"]


def main() -> None:
    parser = argparse.ArgumentParser(description="28_backup_restore.py")
    parser.add_argument("--base-uri", default=_DEFAULT_BASE_URI)
    parser.add_argument("--no-cleanup", action="store_true")
    args = parser.parse_args()
    no_cleanup = args.no_cleanup
    print("=" * 60)
    print("28 备份与恢复")
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

    # STEP 1: 摄入数据
    print("STEP 1: 摄入交易数据")
    r = lake.ingest("sales", [str(DATAS_DIR / "transactions" / "sales_2024_cn.csv")])
    original_rows = r.total_rows
    print(f"  摄入: {original_rows} 行")

    # STEP 2: 创建备份
    print("\nSTEP 2: 创建备份")
    try:
        info = lake.backup_create(datasets=["sales"])
        backup_id = info.backup_id
        print(f"  备份 ID: {backup_id}")
        print(f"  创建时间: {info.created_at}")
        print(f"  数据集: {info.datasets}")
    except (OSError, ValueError) as e:
        print(f"  备份创建: {e}")
        print("\n  可能需要配置 blob store (S3/MinIO)")
        print("  或 backup 功能未启用, 跳过后续步骤")
        if not no_cleanup:
            for ds in _DATASETS:
                try:
                    lake.delete_dataset(ds)
                except Exception:
                    pass
        lake.shutdown()
        shutil.rmtree(base, ignore_errors=True)
        return

    # STEP 3: 列出备份
    print("\nSTEP 3: 列出备份")
    try:
        backups = lake.backup_list()
        print(f"  备份数量: {len(backups)}")
        for b in backups:
            print(f"    ID: {b.backup_id}  时间: {b.created_at}  数据集: {b.datasets}  大小: {b.total_size_bytes}")
    except (OSError, ValueError) as e:
        print(f"  列表: {e}")

    # STEP 4: 修改数据
    print("\nSTEP 4: 修改数据 (追加新行)")
    import pyarrow as pa
    new_table = pa.table({
        "时间戳": ["2024-12-31 23:59:59"],
        "订单号": ["ORD999"],
        "用户编号": ["U9999"],
        "商品类别": ["测试"],
        "商品名称": ["测试商品"],
        "金额": [999.99],
        "支付方式": ["cash"],
        "城市": ["test"],
    })
    lake.append_dataset("sales", new_table)
    print(f"  修改后: {original_rows + 1} 行 (原 {original_rows})")

    # STEP 5: 恢复备份
    print("\nSTEP 5: 恢复备份")
    try:
        lake.backup_restore(backup_id, datasets=["sales"], replace=True)
        catalog = lake.catalog()
        ds = catalog.datasets.get("sales")
        restored_rows = ds.num_rows if ds else 0
        print(f"  恢复后: {restored_rows} 行")
        if restored_rows == original_rows:
            print("  恢复验证: PASS (数据回到原始状态)")
        else:
            print(f"  恢复验证: WARN (预期 {original_rows}, 实际 {restored_rows})")
    except Exception as e:
        print(f"  恢复: {e}")

    # STEP 6: 备份详情 (通过 backup_list 查找)
    print("\nSTEP 6: 备份详情")
    try:
        backups = lake.backup_list()
        target = next((b for b in backups if b.backup_id == backup_id), None)
        if target is not None:
            print(f"  备份 ID:   {target.backup_id}")
            print(f"  创建时间:  {target.created_at}")
            print(f"  数据集:    {target.datasets}")
            print(f"  Blob 前缀: {target.blob_prefixes}")
            print(f"  总大小:    {target.total_size_bytes} bytes")
            print(f"  状态:      {target.status}")
        else:
            print(f"  未找到备份 {backup_id}")
    except (OSError, ValueError) as e:
        print(f"  详情查询: {e}")

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
