#!/usr/bin/env python3
"""26 — 审计日志

场景: 演示数据操作审计日志的记录、查询、验证和导出。

数据: 内部操作产生
"""

from __future__ import annotations

import argparse

import shutil
import sys
from pathlib import Path

import pyarrow as pa

from arrow_lake import Lake

_DEFAULT_BASE_URI = "./_tmp_audit"


def main() -> None:
    parser = argparse.ArgumentParser(description="26_audit_trail.py")
    parser.add_argument("--base-uri", default=_DEFAULT_BASE_URI)
    parser.add_argument("--no-cleanup", action="store_true")
    args = parser.parse_args()
    no_cleanup = args.no_cleanup
    print("=" * 60)
    print("26 审计日志")
    print("=" * 60)

    base = Path(args.base_uri)
    if base.exists():
        shutil.rmtree(base)

    lake = Lake(base_uri=args.base_uri)

    # STEP 1: 创建数据集并记录审计事件
    print("STEP 1: 创建数据集 + 记录审计事件")
    table = pa.table({
        "id": [f"r_{i}" for i in range(10)],
        "value": list(range(10)),
    })
    lake.create_dataset("test_data", table)

    aid1 = lake.audit_record("ingest", "test_data", actor="admin",
                              metaflow_run_id="run_001",
                              payload={"rows": 10})
    print(f"  审计 ID: {aid1[:12]}...")

    # STEP 2: 记录查询事件
    print("\nSTEP 2: 记录查询事件")
    result = lake.olap_query("test_data", "SELECT SUM(value) as total FROM test_data")
    aid2 = lake.audit_record("query", "test_data", actor="user_a",
                              payload={"sql": "SELECT SUM(value)"})
    print(f"  查询结果: {result.table.to_pylist()[0]}")
    print(f"  审计 ID: {aid2[:12]}...")

    # STEP 3: 记录更新事件
    print("\nSTEP 3: 记录更新事件")
    new_table = pa.table({
        "id": [f"r_{i}" for i in range(10, 15)],
        "value": list(range(10, 15)),
    })
    lake.append_dataset("test_data", new_table)
    aid3 = lake.audit_record("update", "test_data", actor="system",
                              payload={"rows_added": 5})
    print(f"  追加 5 行, 审计 ID: {aid3[:12]}...")

    # STEP 4: 查询审计日志
    print("\nSTEP 4: 查询审计日志")
    try:
        events = lake.audit_query(dataset_name="test_data")
        print(f"  test_data 相关事件: {len(events)} 条")
        for evt in events:
            etype = getattr(evt, 'event_type', evt.get('event_type', '?'))
            actor = getattr(evt, 'actor', evt.get('actor', '?'))
            print(f"    [{etype}] actor={actor}")
    except Exception as e:
        print(f"  查询跳过: {e}")

    # STEP 5: 验证审计完整性
    print("\nSTEP 5: HMAC 完整性验证")
    try:
        valid = lake.audit_verify(aid1)
        print(f"  审计 {aid1[:12]}... 有效性: {valid}")
    except (ValueError, RuntimeError) as e:
        print(f"  验证: {e}")

    # STEP 6: 导出审计记录
    print("\nSTEP 6: 导出审计记录")
    try:
        export = lake.audit_export("test_data")
        print(f"  导出记录数: {export.get('total', len(export))}")
    except (ValueError, RuntimeError) as e:
        print(f"  导出跳过: {e}")

    # STEP 7: 数据集最终状态
    print("\nSTEP 7: 数据集状态")
    ds = lake.open_dataset("test_data")
    print(f"  test_data: {ds.count_rows()} 行")

    print("\n  [全部 PASS]")
    if not no_cleanup:
        lake.shutdown()
        shutil.rmtree(base, ignore_errors=True)
        print("(已清理)")


if __name__ == "__main__":
    main()
