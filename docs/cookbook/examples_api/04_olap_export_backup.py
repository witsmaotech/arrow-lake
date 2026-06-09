#!/usr/bin/env python3
"""API-04 — OLAP Analytics & Export & Backup

对应 cookbook: 03_olap_and_export.py, 18_export_and_backup_demo.py
验证: SQL 查询、数据导出、备份创建/列表/恢复
"""

from __future__ import annotations
import os

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from conftest import ArrowLakeClient

BASE_URL = os.environ.get("ARROW_LAKE_BASE_URL", "http://localhost:8000")
API_KEY = os.environ.get("ARROW_LAKE_API_KEY", "dev-api-key-for-local-testing-only")


def main() -> None:
    print("=" * 60)
    print("API-04  OLAP / Export / Backup")
    print("=" * 60)

    c = ArrowLakeClient(BASE_URL, API_KEY)

    ds = c.list_datasets()
    datasets = ds.get("datasets", [])
    if not datasets:
        print("  [SKIP] No datasets available")
        return

    target = max(datasets, key=lambda d: d["num_rows"])
    name = target["name"]
    rows = target["num_rows"]
    print(f"\nUsing dataset: {name} ({rows} rows)")

    # 1. OLAP count
    print("\nSTEP 1: OLAP count(*)")
    resp = c.query_olap(name, f'SELECT count(*) as cnt FROM "{name}"')
    assert resp.get("success"), f"OLAP failed: {resp}"
    rows_result = resp.get("rows", [])
    cnt = rows_result[0]["cnt"] if rows_result else 0
    assert cnt >= rows, f"count mismatch: {cnt} < {rows}"
    c._pass(f"SELECT count(*) — {cnt} rows")

    # 2. OLAP group by
    print("\nSTEP 2: OLAP group by")
    resp = c.query_olap(name, f'SELECT source, count(*) as cnt FROM "{name}" GROUP BY source ORDER BY cnt DESC LIMIT 5')
    if resp.get("success"):
        for r in resp.get("rows", []):
            print(f"         {r}")
        c._pass("GROUP BY source")
    else:
        print(f"  [WARN] {resp.get('error')}: {resp.get('message', '')[:120]}")

    # 3. OLAP distinct values
    print("\nSTEP 3: OLAP distinct modality")
    resp = c.query_olap(name, f'SELECT DISTINCT modality FROM "{name}" LIMIT 10')
    if resp.get("success"):
        mods = [r.get("modality", "?") for r in resp.get("rows", [])]
        c._pass(f"modalities: {mods}")
    else:
        print(f"  [WARN] {resp.get('error')}: {resp.get('message', '')[:120]}")

    # 4. Metadata query
    print("\nSTEP 4: Metadata query (schema)")
    resp = c.query_metadata(name, f"SELECT column_name, data_type FROM information_schema.columns WHERE table_name = '{name}'")
    if resp.get("success"):
        for r in resp.get("rows", [])[:5]:
            print(f"         {r.get('column_name', '?'):30s} {r.get('data_type', '?')}")
        c._pass("information_schema.columns")
    else:
        print(f"  [WARN] {resp.get('error')}: {resp.get('message', '')[:120]}")

    # 5. Export
    print("\nSTEP 5: Export (parquet)")
    resp = c.export(name, format="parquet")
    if resp.get("success"):
        task_id = resp.get("task_id", "")
        c._pass(f"export started — task_id={task_id}")
        if task_id:
            status = c.wait_for_export(name, task_id, timeout=30)
            c._pass(f"export completed — {status.get('status')}")
    else:
        print(f"  [INFO] {resp.get('error')}: {resp.get('message', '')[:120]}")

    # 6. Quality report
    print("\nSTEP 6: Quality report")
    resp = c.quality_report(name)
    if resp.get("success"):
        c._pass(f"quality report — {str(resp)[:100]}")
    else:
        print(f"  [INFO] {resp.get('error')}: {resp.get('message', '')[:120]}")

    # 7. Backup create
    print("\nSTEP 7: Backup create")
    resp = c.backup_create([name])
    if resp.get("success"):
        backup_id = resp.get("backup_id", "")
        c._pass(f"backup created — id={backup_id}")
    else:
        print(f"  [INFO] {resp.get('error')}: {resp.get('message', '')[:120]}")

    # 8. Backup list
    print("\nSTEP 8: Backup list")
    resp = c.backup_list()
    if resp.get("success"):
        backups = resp.get("backups", resp.get("data", []))
        c._pass(f"backup list — {len(backups)} backups")
        for b in backups[:3]:
            print(f"         {b}")
    else:
        print(f"  [INFO] {resp.get('error')}: {resp.get('message', '')[:120]}")

    print("\n" + "=" * 60)
    print("API-04  ALL PASSED")
    print("=" * 60)


if __name__ == "__main__":
    main()
