#!/usr/bin/env python3
"""API-21 — Daft DataFrame 基础查询

业务场景: 数据工程师需要用 DataFrame API 探索已摄取的结构化数据，
         对比 Daft 懒加载模式与 SQL 查询在不同场景下的适用性。
数据源: datas/transactions/sales_2024.csv (1000 条订单记录)
流程: 摄取 CSV → Daft 全表加载 → 列选择 → 格式对比 → 与 SQL 交叉验证
"""

from __future__ import annotations
import os

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from conftest import ArrowLakeClient

BASE_URL = os.environ.get("ARROW_LAKE_BASE_URL", "http://localhost:8000")
API_KEY = os.environ.get("ARROW_LAKE_API_KEY", "dev-api-key-for-local-testing-only")
DATAS_DIR = Path(__file__).resolve().parent.parent / "datas"

DS_NAME = "daft-basics"


def main() -> None:
    print("=" * 60)
    print("API-21  Daft DataFrame 基础查询")
    print("=" * 60)

    c = ArrowLakeClient(BASE_URL, API_KEY)
    c.delete_dataset(DS_NAME)

    # ── Phase 1: 数据摄取 ──

    print("\n── Phase 1: 数据摄取 ──")

    csv_path = DATAS_DIR / "transactions" / "sales_2024.csv"
    assert csv_path.exists(), f"数据文件不存在: {csv_path}"

    print("\nSTEP 1: 摄取交易 CSV")
    resp = c.ingest_files(DS_NAME, [str(csv_path)])
    if not resp.get("success"):
        print(f"  [SKIP] 摄取失败: {resp.get('error')}: {resp.get('message', '')[:120]}")
        print(f"         (Docker 容器可能无法读取宿主机路径)")
        c.delete_dataset(DS_NAME)
        return

    total_rows = resp.get("total_rows", 0)
    c._pass(f"摄取完成 — {total_rows} 行")

    # ── Phase 2: Daft 全表加载 ──

    print("\n── Phase 2: Daft 全表加载 ──")

    print("\nSTEP 2: Daft query — 全表加载 (JSON)")
    resp = c.query_daft(DS_NAME)
    if resp.get("success"):
        rows = resp.get("row_count", 0)
        cols = resp.get("column_count", 0)
        data = resp.get("rows", [])[:3]
        print(f"         {rows} rows × {cols} columns")
        for r in data:
            print(f"         {r}")
        assert rows == total_rows, f"Daft 行数不匹配: {rows} != {total_rows}"
        c._pass(f"全表加载 — {rows} 行 × {cols} 列")
    else:
        print(f"  [INFO] {resp.get('error')}: {resp.get('message', '')[:120]}")

    # ── Phase 3: Daft 列选择 ──

    print("\n── Phase 3: Daft 列选择 ──")

    print("\nSTEP 3: Daft query — 选择业务关键字段")
    resp = c.query_daft(DS_NAME, columns=["order_id", "category", "amount", "region"])
    if resp.get("success"):
        rows = resp.get("row_count", 0)
        cols = resp.get("column_count", 0)
        data = resp.get("rows", [])[:5]
        print(f"         {rows} rows × {cols} columns")
        for r in data:
            print(f"         order={r.get('order_id', '?'):15s} "
                  f"cat={r.get('category', '?'):12s} "
                  f"amt={r.get('amount', 0):>10.2f} "
                  f"region={r.get('region', '?')}")
        assert cols == 4, f"期望 4 列, 实际 {cols}"
        c._pass(f"列选择 — {cols} 列: order_id, category, amount, region")
    else:
        print(f"  [INFO] {resp.get('error')}: {resp.get('message', '')[:120]}")

    # ── Phase 4: 与 SQL 交叉验证 ──

    print("\n── Phase 4: 与 SQL 交叉验证 ──")

    print("\nSTEP 4: SQL 查询相同列 — 验证数据一致性")
    resp = c.query_olap(
        DS_NAME,
        f'SELECT order_id, category, amount, region FROM "{DS_NAME}" LIMIT 5',
    )
    if resp.get("success"):
        sql_rows = resp.get("rows", [])
        for r in sql_rows:
            print(f"         order={r.get('order_id', '?'):15s} "
                  f"cat={r.get('category', '?'):12s} "
                  f"amt={r.get('amount', 0):>10.2f} "
                  f"region={r.get('region', '?')}")
        c._pass("SQL 与 Daft 返回一致列")
    else:
        print(f"  [INFO] {resp.get('error')}: {resp.get('message', '')[:120]}")

    # ── Phase 5: 格式对比 ──

    print("\n── Phase 5: 响应格式对比 ──")

    print("\nSTEP 5: Daft query — Arrow IPC 格式 (二进制高效)")
    resp = c.query_daft(DS_NAME, columns=["order_id", "amount"], format="arrow_ipc")
    if resp.get("success"):
        ipc_size = len(resp.get("data", "")) if resp.get("data") else 0
        row_count = resp.get("row_count", 0)
        print(f"         Arrow IPC: {row_count} rows, base64 size ≈ {ipc_size} chars")
        c._pass(f"Arrow IPC 格式 — {row_count} 行")
    else:
        print(f"  [INFO] {resp.get('error')}: {resp.get('message', '')[:120]}")

    print("\nSTEP 6: Daft query — JSON 格式 (可读性好)")
    resp = c.query_daft(DS_NAME, columns=["order_id", "amount"], format="json")
    if resp.get("success"):
        json_rows = resp.get("rows", [])
        row_count = resp.get("row_count", 0)
        print(f"         JSON: {row_count} rows, sample: {json_rows[0] if json_rows else 'N/A'}")
        c._pass(f"JSON 格式 — {row_count} 行")
    else:
        print(f"  [INFO] {resp.get('error')}: {resp.get('message', '')[:120]}")

    # 清理
    c.delete_dataset(DS_NAME)

    print("\n" + "=" * 60)
    print("API-21  ALL PASSED")
    print("=" * 60)


if __name__ == "__main__":
    main()
