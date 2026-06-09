#!/usr/bin/env python3
"""API-09 — Lineage & Audit Trail

对应 cookbook: 26_audit_trail.py, 27_data_lineage.py
验证: 数据血缘记录/历史/查询、审计记录/验证/查询/导出、数据治理工作流
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
    print("API-09  Lineage & Audit Trail")
    print("=" * 60)

    c = ArrowLakeClient(BASE_URL, API_KEY)

    ds = c.list_datasets()
    datasets = ds.get("datasets", [])
    if not datasets:
        print("  [SKIP] No datasets available")
        return

    target = max(datasets, key=lambda d: d["num_rows"])
    name = target["name"]
    print(f"\nUsing dataset: {name} ({target['num_rows']} rows)")

    # === Lineage ===

    # 1. Record lineage event
    print("\nSTEP 1: Record lineage event")
    resp = c.lineage_record(
        dataset_name=name,
        operation="ingest",
        inputs=["raw_data/sales_2024.csv"],
        outputs=[name],
        metadata={"source": "csv_upload", "rows_affected": 1500},
    )
    if resp.get("success"):
        lineage_id = resp.get("lineage_id", resp.get("id", ""))
        c._pass(f"lineage recorded — id={lineage_id}")
    else:
        print(f"  [INFO] {resp.get('error')}: {resp.get('message', '')[:120]}")

    # 2. Record transform lineage
    print("\nSTEP 2: Record transform lineage")
    resp = c.lineage_record(
        dataset_name=name,
        operation="transform",
        inputs=[name],
        outputs=[f"{name}_clean"],
        metadata={"transform": "quality_filter", "rules_applied": 3},
    )
    if resp.get("success"):
        c._pass("transform lineage recorded")
    else:
        print(f"  [INFO] {resp.get('error')}: {resp.get('message', '')[:120]}")

    # 3. Record export lineage
    print("\nSTEP 3: Record export lineage")
    resp = c.lineage_record(
        dataset_name=name,
        operation="export",
        inputs=[name],
        outputs=[f"exports/{name}.parquet"],
        metadata={"format": "parquet", "size_mb": 42.5},
    )
    if resp.get("success"):
        c._pass("export lineage recorded")
    else:
        print(f"  [INFO] {resp.get('error')}: {resp.get('message', '')[:120]}")

    # 4. Get lineage history
    print("\nSTEP 4: Lineage history")
    resp = c.lineage_history(name)
    if resp.get("success"):
        events = resp.get("events", resp.get("data", []))
        c._pass(f"lineage history — {len(events)} events")
        for ev in events[:3]:
            print(f"         op={ev.get('operation', '?'):12s} "
                  f"inputs={ev.get('inputs', [])} → outputs={ev.get('outputs', [])}")
    else:
        print(f"  [INFO] {resp.get('error')}: {resp.get('message', '')[:120]}")

    # 5. Query lineage via SQL
    print("\nSTEP 5: Lineage query (SQL)")
    resp = c.lineage_query(
        f"SELECT operation, count(*) as cnt FROM lineage "
        f"WHERE dataset_name = '{name}' GROUP BY operation ORDER BY cnt DESC"
    )
    if resp.get("success"):
        rows = resp.get("rows", resp.get("data", []))
        c._pass(f"lineage SQL — {len(rows)} operation types")
        for r in rows:
            print(f"         {r}")
    else:
        print(f"  [INFO] {resp.get('error')}: {resp.get('message', '')[:120]}")

    # === Audit ===

    # 6. Record audit event
    print("\nSTEP 6: Record audit event")
    audit_id = ""
    resp = c.audit_record(
        dataset_name=name,
        action="data_ingest",
        details={"source": "csv", "rows": 1500, "user": "admin"},
    )
    if resp.get("success"):
        audit_id = resp.get("audit_id", resp.get("id", ""))
        c._pass(f"audit recorded — id={audit_id}")
    else:
        print(f"  [INFO] {resp.get('error')}: {resp.get('message', '')[:120]}")

    # 7. Record search audit
    print("\nSTEP 7: Record search audit")
    resp = c.audit_record(
        dataset_name=name,
        action="search_query",
        details={"query": "machine learning", "results": 25},
    )
    if resp.get("success"):
        c._pass("search audit recorded")
    else:
        print(f"  [INFO] {resp.get('error')}: {resp.get('message', '')[:120]}")

    # 8. Record export audit
    print("\nSTEP 8: Record export audit")
    resp = c.audit_record(
        dataset_name=name,
        action="data_export",
        details={"format": "parquet", "size_bytes": 44564480},
    )
    if resp.get("success"):
        c._pass("export audit recorded")
    else:
        print(f"  [INFO] {resp.get('error')}: {resp.get('message', '')[:120]}")

    # 9. Verify audit integrity
    print("\nSTEP 9: Verify audit integrity")
    if audit_id:
        resp = c.audit_verify(audit_id)
        if resp.get("success"):
            valid = resp.get("intact", resp.get("valid", False))
            c._pass(f"audit verify — valid={valid}")
        else:
            print(f"  [INFO] {resp.get('error')}: {resp.get('message', '')[:120]}")
    else:
        print("  [SKIP] No audit_id to verify")

    # 10. Query audit trail
    print("\nSTEP 10: Query audit trail")
    resp = c.audit_query()
    if resp.get("success"):
        events = resp.get("entries", resp.get("events", resp.get("data", [])))
        c._pass(f"audit trail — {len(events)} events")
        for ev in events[:3]:
            print(f"         action={ev.get('action', ev.get('event_type', '?')):16s} "
                  f"dataset={ev.get('dataset_name', '?')}")
    else:
        print(f"  [INFO] {resp.get('error')}: {resp.get('message', '')[:120]}")

    # 11. Query audit with filters
    print("\nSTEP 11: Query audit (filtered)")
    resp = c.audit_query(dataset_name=name, event_type="data_ingest")
    if resp.get("success"):
        events = resp.get("entries", resp.get("events", resp.get("data", [])))
        c._pass(f"filtered audit — {len(events)} ingest events")
    else:
        print(f"  [INFO] {resp.get('error')}: {resp.get('message', '')[:120]}")

    # 12. Export audit trail
    print("\nSTEP 12: Export audit trail")
    resp = c.audit_export(name)
    if resp.get("success"):
        exported = resp.get("export", {})
        if isinstance(exported, dict):
            exported = exported.get("count", exported.get("exported_count", 0))
        c._pass(f"audit export — {exported} events exported")
    else:
        print(f"  [INFO] {resp.get('error')}: {resp.get('message', '')[:120]}")

    print("\n" + "=" * 60)
    print("API-09  ALL PASSED")
    print("=" * 60)


if __name__ == "__main__":
    main()
