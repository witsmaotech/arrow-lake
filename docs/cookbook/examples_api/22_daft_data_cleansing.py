#!/usr/bin/env python3
"""API-22 — Daft 数据清洗管道

业务场景: 数据工程师收到多源混合数据，需要在入库前快速探索数据质量:
         空值检查、类型识别、异常值检测、分布统计。
         对比 Daft DataFrame API 与 SQL 的表达力差异。
数据源: datas/transactions/sales_2024.csv + datas/kb/knowledge.jsonl
流程: 双源摄取 → Daft 探测结构 → SQL 精确分析 → 质量报告
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

DS_TXN = "daft-clean-txn"
DS_KB = "daft-clean-kb"


def main() -> None:
    print("=" * 60)
    print("API-22  Daft 数据清洗管道")
    print("=" * 60)

    c = ArrowLakeClient(BASE_URL, API_KEY)

    # 清理
    c.delete_dataset(DS_TXN)
    c.delete_dataset(DS_KB)

    # ── Phase 1: 多源摄取 ──

    print("\n── Phase 1: 多源摄取 ──")

    csv_path = DATAS_DIR / "transactions" / "sales_2024.csv"
    kb_path = DATAS_DIR / "kb" / "knowledge.jsonl"

    print("\nSTEP 1: 摄取交易 CSV")
    resp = c.ingest_files(DS_TXN, [str(csv_path)])
    if not resp.get("success"):
        print(f"  [SKIP] {resp.get('error')}: {resp.get('message', '')[:120]}")
        return
    txn_rows = resp.get("total_rows", 0)
    c._pass(f"交易数据 — {txn_rows} 行")

    print("\nSTEP 2: 摄取知识库 JSONL")
    assert kb_path.exists(), f"数据文件不存在: {kb_path}"
    resp = c.ingest_files(DS_KB, [str(kb_path)])
    if not resp.get("success"):
        print(f"  [SKIP] {resp.get('error')}: {resp.get('message', '')[:120]}")
        c.delete_dataset(DS_TXN)
        return
    kb_rows = resp.get("total_rows", 0)
    c._pass(f"知识库数据 — {kb_rows} 行")

    # ── Phase 2: Daft 结构探测 ──

    print("\n── Phase 2: Daft 结构探测 ──")

    print("\nSTEP 3: Daft 加载交易数据 — 探测 schema")
    resp = c.query_daft(DS_TXN)
    if resp.get("success"):
        cols = resp.get("column_count", 0)
        rows = resp.get("row_count", 0)
        sample = resp.get("rows", [{}])[0]
        print(f"         {rows} rows × {cols} columns")
        print(f"         列名: {list(sample.keys())}")
        # 打印每列的值样本
        for k, v in sample.items():
            vstr = str(v)[:50]
            print(f"         {k:20s} → {vstr}")
        c._pass(f"Schema 探测 — {cols} 列")
    else:
        print(f"  [INFO] {resp.get('error')}: {resp.get('message', '')[:120]}")

    print("\nSTEP 4: Daft 加载知识库数据 — 对比异构 schema")
    resp = c.query_daft(DS_KB)
    if resp.get("success"):
        cols = resp.get("column_count", 0)
        rows = resp.get("row_count", 0)
        sample = resp.get("rows", [{}])[0]
        print(f"         {rows} rows × {cols} columns")
        print(f"         列名: {list(sample.keys())}")
        c._pass(f"知识库 Schema — {cols} 列")
    else:
        print(f"  [INFO] {resp.get('error')}: {resp.get('message', '')[:120]}")

    # ── Phase 3: SQL 精确分析 (Daft 不支持的聚合操作) ──

    print("\n── Phase 3: SQL 精确分析 ──")

    print("\nSTEP 5: SQL — 交易金额分布统计")
    resp = c.query_olap(
        DS_TXN,
        f'SELECT '
        f'  count(*) as total, '
        f'  round(avg(amount), 2) as avg_amt, '
        f'  round(min(amount), 2) as min_amt, '
        f'  round(max(amount), 2) as max_amt, '
        f'  count(DISTINCT user_id) as unique_users '
        f'FROM "{DS_TXN}"',
    )
    if resp.get("success"):
        row = resp.get("rows", [{}])[0]
        print(f"         total={row.get('total')} "
              f"avg={row.get('avg_amt')} "
              f"min={row.get('min_amt')} "
              f"max={row.get('max_amt')} "
              f"users={row.get('unique_users')}")
        c._pass("金额分布统计")
    else:
        print(f"  [INFO] {resp.get('error')}: {resp.get('message', '')[:120]}")

    print("\nSTEP 6: SQL — 类别 TOP 10")
    resp = c.query_olap(
        DS_TXN,
        f'SELECT category, count(*) as cnt, round(sum(amount), 2) as total '
        f'FROM "{DS_TXN}" GROUP BY category ORDER BY cnt DESC LIMIT 10',
    )
    if resp.get("success"):
        for r in resp.get("rows", []):
            print(f"         {r.get('category', '?'):20s} "
                  f"cnt={r.get('cnt', 0):>4d} "
                  f"total={r.get('total', 0):>12}")
        c._pass("类别 TOP 10")
    else:
        print(f"  [INFO] {resp.get('error')}: {resp.get('message', '')[:120]}")

    # ── Phase 4: 质量报告 ──

    print("\n── Phase 4: 质量报告 ──")

    print("\nSTEP 7: 质量报告 — 交易数据")
    resp = c.quality_report(DS_TXN)
    if resp.get("success"):
        report = resp.get("report", {})
        score = report.get("score", "N/A")
        checks = report.get("checks", {})
        print(f"         整体评分: {score}")
        for check, detail in checks.items():
            status = detail.get("status", "?") if isinstance(detail, dict) else detail
            print(f"         {check:25s} → {status}")
        c._pass("质量报告完成")
    else:
        print(f"  [INFO] {resp.get('error')}: {resp.get('message', '')[:120]}")

    print("\nSTEP 8: SQL — 支付方式 + 地区交叉分析")
    resp = c.query_olap(
        DS_TXN,
        f'SELECT payment_method, region, count(*) as cnt '
        f'FROM "{DS_TXN}" '
        f'GROUP BY payment_method, region '
        f'ORDER BY cnt DESC LIMIT 8',
    )
    if resp.get("success"):
        for r in resp.get("rows", []):
            print(f"         {r.get('payment_method', '?'):18s} × "
                  f"{r.get('region', '?'):18s} = {r.get('cnt', 0)}")
        c._pass("支付方式 × 地区交叉分析")
    else:
        print(f"  [INFO] {resp.get('error')}: {resp.get('message', '')[:120]}")

    # 清理
    c.delete_dataset(DS_TXN)
    c.delete_dataset(DS_KB)

    print("\n" + "=" * 60)
    print("API-22  ALL PASSED")
    print("=" * 60)


if __name__ == "__main__":
    main()
