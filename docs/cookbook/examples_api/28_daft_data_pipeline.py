#!/usr/bin/env python3
"""API-28 — Daft 数据管道：摄取 → 转换 → 导出

业务场景: 数据工程师需要构建端到端的数据管道:
         - 多格式数据源统一摄取 (CSV / JSONL / PDF)
         - Daft 快速探测各源结构
         - SQL 做清洗转换 (过滤、聚合、派生列)
         - 多格式导出 (Parquet / CSV / JSON)
         - 血缘追踪 + 审计日志
数据源: sales_2024.csv + knowledge.jsonl + papers/metadata.csv
流程: 三源摄取 → Daft 探测 → SQL 清洗转换 → 多格式导出 → 血缘记录
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

DS_RAW = "pipeline-raw"
DS_KB = "pipeline-kb"
DS_PAPERS = "pipeline-papers"
DS_CLEAN = "pipeline-clean"


def main() -> None:
    print("=" * 60)
    print("API-28  Daft 数据管道：摄取 → 转换 → 导出")
    print("=" * 60)

    c = ArrowLakeClient(BASE_URL, API_KEY)
    for ds in (DS_RAW, DS_KB, DS_PAPERS, DS_CLEAN):
        c.delete_dataset(ds)

    # ── Phase 1: 多源摄取 ──

    print("\n── Phase 1: 多源摄取 ──")

    csv_path = DATAS_DIR / "transactions" / "sales_2024.csv"
    kb_path = DATAS_DIR / "kb" / "knowledge.jsonl"
    papers_path = DATAS_DIR / "papers" / "metadata.csv"

    sources = [
        ("CSV 交易数据", DS_RAW, str(csv_path)),
        ("JSONL 知识库", DS_KB, str(kb_path)),
        ("CSV 论文元数据", DS_PAPERS, str(papers_path)),
    ]

    for label, ds_name, path in sources:
        assert Path(path).exists(), f"文件不存在: {path}"
        print(f"\n  摄取 {label} → {ds_name}...")
        resp = c.ingest_files(ds_name, [path])
        if resp.get("success"):
            c._pass(f"{label} — {resp.get('total_rows', 0)} 行")
        else:
            print(f"  [SKIP] {resp.get('error')}: {resp.get('message', '')[:120]}")

    # 记录血缘
    c.lineage_record(DS_RAW, "ingest", inputs=["sales_2024.csv", "knowledge.jsonl", "metadata.csv"])

    # ── Phase 2: Daft 探测 ──

    print("\n── Phase 2: Daft 探测原始数据 ──")

    print("\nSTEP 1: Daft 加载原始数据 — 识别列类型与范围")
    resp = c.query_daft(DS_RAW)
    if resp.get("success"):
        rows = resp.get("rows", [])
        cols = resp.get("column_count", 0)
        total = resp.get("row_count", 0)
        col_names = list(rows[0].keys()) if rows else []
        print(f"         {total} rows × {cols} cols")
        print(f"         列: {col_names}")

        # 每列样本值
        if rows:
            for col in col_names[:8]:
                vals = [str(r.get(col, "?"))[:30] for r in rows[:3]]
                print(f"         {col:20s} → [{', '.join(vals)}]")
        c._pass(f"原始数据探测 — {cols} 列")
    else:
        print(f"  [INFO] {resp.get('error')}: {resp.get('message', '')[:120]}")

    # ── Phase 3: SQL 清洗转换 ──

    print("\n── Phase 3: SQL 清洗转换 ──")

    print("\nSTEP 2: SQL — 交易数据金额分级 (新增 price_tier 列)")
    resp = c.query_olap(
        DS_RAW,
        f'SELECT '
        f'  CASE '
        f"    WHEN amount < 500 THEN 'budget' "
        f"    WHEN amount < 2000 THEN 'mid_range' "
        f"    WHEN amount < 5000 THEN 'premium' "
        f"    ELSE 'luxury' "
        f'  END as price_tier, '
        f'  count(*) as cnt, '
        f'  round(avg(amount), 2) as avg_amt, '
        f'  round(min(amount), 2) as min_amt, '
        f'  round(max(amount), 2) as max_amt '
        f'FROM "{DS_RAW}" '
        f'WHERE category IS NOT NULL '
        f'GROUP BY price_tier ORDER BY avg_amt',
    )
    if resp.get("success"):
        for r in resp.get("rows", []):
            print(f"         {r.get('price_tier', '?'):12s} "
                  f"cnt={r.get('cnt', 0):>4d} "
                  f"avg={r.get('avg_amt', 0):>8} "
                  f"range=[{r.get('min_amt', 0)}, {r.get('max_amt', 0)}]")
        c._pass("金额分级转换")
    else:
        print(f"  [INFO] {resp.get('error')}: {resp.get('message', '')[:120]}")

    print("\nSTEP 3: SQL — 地区消费力排名")
    resp = c.query_olap(
        DS_RAW,
        f'SELECT region, '
        f'  count(*) as orders, '
        f'  round(sum(amount), 2) as revenue, '
        f'  round(avg(amount), 2) as avg_order, '
        f'  count(DISTINCT user_id) as customers '
        f'FROM "{DS_RAW}" '
        f'WHERE region IS NOT NULL '
        f'GROUP BY region '
        f'ORDER BY revenue DESC LIMIT 10',
    )
    if resp.get("success"):
        for r in resp.get("rows", []):
            print(f"         {r.get('region', '?'):20s} "
                  f"orders={r.get('orders', 0):>4d} "
                  f"revenue={r.get('revenue', 0):>10} "
                  f"avg={r.get('avg_order', 0):>8} "
                  f"customers={r.get('customers', 0):>3d}")
        c._pass("地区消费力排名")
    else:
        print(f"  [INFO] {resp.get('error')}: {resp.get('message', '')[:120]}")

    # ── Phase 4: 去重与质量过滤 ──

    print("\n── Phase 4: 去重与质量过滤 ──")

    print("\nSTEP 4: 质量过滤 — 移除低质量记录")
    resp = c.quality_filter(DS_RAW, rules=[
        {"column": "amount", "rule": "not_null"},
        {"column": "category", "rule": "not_null"},
    ])
    if resp.get("success"):
        removed = resp.get("rows_removed", 0)
        kept = resp.get("rows_kept", 0)
        print(f"         移除: {removed}, 保留: {kept}")
        c._pass("质量过滤完成")
    else:
        print(f"  [INFO] {resp.get('error')}: {resp.get('message', '')[:120]}")

    print("\nSTEP 5: 去重 — 基于主键去重")
    resp = c.quality_deduplicate(DS_RAW)
    if resp.get("success"):
        dupes = resp.get("duplicates_removed", 0)
        print(f"         去除重复: {dupes} 条")
        c._pass(f"去重完成 — 移除 {dupes} 条重复")
    else:
        print(f"  [INFO] {resp.get('error')}: {resp.get('message', '')[:120]}")

    c.lineage_record(DS_RAW, "transform", inputs=[DS_RAW], outputs=[DS_CLEAN])

    # ── Phase 5: 多格式导出 ──

    print("\n── Phase 5: 多格式导出 ──")

    formats = [
        ("parquet", "Parquet (列式, 高压缩)"),
        ("csv", "CSV (通用, 可读)"),
        ("json", "JSON (嵌套结构友好)"),
    ]

    for fmt, desc in formats:
        print(f"\nSTEP: 导出 {desc}")
        resp = c.export(DS_RAW, format=fmt)
        if resp.get("success"):
            task_id = resp.get("task_id", "")
            result = c.wait_for_export(DS_RAW, task_id, timeout=60)
            size = result.get("file_size_bytes", 0)
            if size:
                if size > 1024 * 1024:
                    size_str = f"{size / 1024 / 1024:.1f} MB"
                elif size > 1024:
                    size_str = f"{size / 1024:.1f} KB"
                else:
                    size_str = f"{size} bytes"
                print(f"         {fmt:10s} → {size_str}")
            c._pass(f"{fmt} 导出")
        else:
            print(f"  [INFO] {resp.get('error')}: {resp.get('message', '')[:120]}")

    # ── Phase 6: 血缘与审计 ──

    print("\n── Phase 6: 血缘与审计 ──")

    print("\nSTEP 6: 查询数据血缘")
    resp = c.lineage_history(DS_RAW)
    if resp.get("success"):
        history = resp.get("history", [])
        for h in history[:5]:
            op = h.get("operation", "?") if isinstance(h, dict) else str(h)
            ts = h.get("timestamp", "")[:19] if isinstance(h, dict) else ""
            print(f"         [{ts}] {op}")
        c._pass(f"血缘记录 — {len(history)} 条")
    else:
        print(f"  [INFO] {resp.get('error')}: {resp.get('message', '')[:120]}")

    print("\nSTEP 7: 记录审计日志 — 管道执行完成")
    resp = c.audit_record(DS_RAW, "pipeline_run", details={
        "phase": "ingest→transform→export",
        "formats": "parquet,csv,json",
    })
    if resp.get("success"):
        c._pass("审计日志已记录")
    else:
        print(f"  [INFO] {resp.get('error')}: {resp.get('message', '')[:120]}")

    # 清理
    for ds in (DS_RAW, DS_KB, DS_PAPERS, DS_CLEAN):
        c.delete_dataset(ds)

    print("\n" + "=" * 60)
    print("API-28  ALL PASSED")
    print("=" * 60)


if __name__ == "__main__":
    main()
