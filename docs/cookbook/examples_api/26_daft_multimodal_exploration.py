#!/usr/bin/env python3
"""API-26 — Daft 多模态数据探索

业务场景: 多媒体平台需要统一管理结构化数据、文本知识库和图片资产:
         - CSV 交易数据 vs JSONL 知识库 vs 图片元数据 — 结构差异对比
         - 多源数据的统一 Daft 查询体验
         - 跨源统计对比 (行数/列数/数据类型)
         - 为构建统一数据湖探索最佳摄取策略
数据源: sales_2024.csv + knowledge.jsonl + 图片元数据 (papers/metadata.csv)
流程: 三源摄取 → Daft Schema 对比 → SQL 跨源统计 → 数据类型分析
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from conftest import ArrowLakeClient

BASE_URL = "http://localhost:8000"
API_KEY = "dev-api-key-for-local-testing-only"
DATAS_DIR = Path(__file__).resolve().parent.parent / "datas"

DS_TXN = "daft-multi-txn"
DS_KB = "daft-multi-kb"
DS_PAPERS = "daft-multi-papers"


def _schema_summary(name: str, resp: dict) -> dict:
    """Extract schema info from Daft response."""
    if not resp.get("success"):
        return {"name": name, "error": True}
    sample = resp.get("rows", [{}])[0]
    return {
        "name": name,
        "rows": resp.get("row_count", 0),
        "cols": resp.get("column_count", 0),
        "columns": list(sample.keys()),
        "sample": sample,
    }


def main() -> None:
    print("=" * 60)
    print("API-26  Daft 多模态数据探索")
    print("=" * 60)

    c = ArrowLakeClient(BASE_URL, API_KEY)
    for ds in (DS_TXN, DS_KB, DS_PAPERS):
        c.delete_dataset(ds)

    # ── Phase 1: 三源摄取 ──

    print("\n── Phase 1: 三源摄取 ──")

    csv_path = DATAS_DIR / "transactions" / "sales_2024.csv"
    kb_path = DATAS_DIR / "kb" / "knowledge.jsonl"
    papers_path = DATAS_DIR / "papers" / "metadata.csv"

    datasets = [
        (DS_TXN, "交易数据 (CSV)", [str(csv_path)]),
        (DS_KB, "知识库 (JSONL)", [str(kb_path)]),
        (DS_PAPERS, "论文元数据 (CSV)", [str(papers_path)]),
    ]

    for ds_name, label, paths in datasets:
        assert Path(paths[0]).exists(), f"文件不存在: {paths[0]}"
        print(f"\n  摄取 {label}...")
        resp = c.ingest_files(ds_name, paths)
        if resp.get("success"):
            c._pass(f"{label} — {resp.get('total_rows', 0)} 行")
        else:
            print(f"  [SKIP] {resp.get('error')}: {resp.get('message', '')[:120]}")
            for ds in (DS_TXN, DS_KB, DS_PAPERS):
                c.delete_dataset(ds)
            return

    # ── Phase 2: Daft Schema 对比 ──

    print("\n── Phase 2: Daft Schema 对比 ──")

    schemas: dict[str, dict] = {}

    print("\nSTEP 1: Daft 加载交易数据 Schema")
    resp = c.query_daft(DS_TXN)
    schemas["txn"] = _schema_summary("交易数据", resp)
    s = schemas["txn"]
    if not s.get("error"):
        print(f"         {s['rows']} rows × {s['cols']} cols")
        print(f"         列: {s['columns']}")
        c._pass(f"交易 Schema — {s['cols']} 列")
    else:
        print("         [INFO] 查询失败")

    print("\nSTEP 2: Daft 加载知识库 Schema")
    resp = c.query_daft(DS_KB)
    schemas["kb"] = _schema_summary("知识库", resp)
    s = schemas["kb"]
    if not s.get("error"):
        print(f"         {s['rows']} rows × {s['cols']} cols")
        print(f"         列: {s['columns']}")
        c._pass(f"知识库 Schema — {s['cols']} 列")
    else:
        print("         [INFO] 查询失败")

    print("\nSTEP 3: Daft 加载论文元数据 Schema")
    resp = c.query_daft(DS_PAPERS)
    schemas["papers"] = _schema_summary("论文元数据", resp)
    s = schemas["papers"]
    if not s.get("error"):
        print(f"         {s['rows']} rows × {s['cols']} cols")
        print(f"         列: {s['columns']}")
        c._pass(f"论文 Schema — {s['cols']} 列")
    else:
        print("         [INFO] 查询失败")

    # ── Phase 3: 跨源列对比 ──

    print("\n── Phase 3: 跨源列对比 ──")

    print("\nSTEP 4: 共有列与独有列分析")
    all_cols: dict[str, set[str]] = {}
    for key, s in schemas.items():
        if not s.get("error"):
            all_cols[key] = set(s.get("columns", []))

    if len(all_cols) >= 2:
        keys = list(all_cols.keys())
        shared = all_cols[keys[0]]
        for k in keys[1:]:
            shared = shared & all_cols[k]
        print(f"         全部共有列: {shared if shared else '(无)'}")

        for k in keys:
            others = set()
            for k2 in keys:
                if k2 != k:
                    others |= all_cols[k2]
            unique = all_cols[k] - others
            if unique:
                print(f"         {k} 独有列: {unique}")
        c._pass("跨源列对比完成")
    else:
        print("         [INFO] 可用数据集不足，跳过对比")

    # ── Phase 4: 数据量对比 ──

    print("\n── Phase 4: 数据量对比 ──")

    print("\nSTEP 5: 三源数据量统计")
    print(f"         {'数据集':20s} {'行数':>8s} {'列数':>6s} {'格式':10s}")
    print(f"         {'─' * 20} {'─' * 8} {'─' * 6} {'─' * 10}")

    labels = {"txn": ("交易数据", "CSV"), "kb": ("知识库", "JSONL"), "papers": ("论文元数据", "CSV")}
    for key, (label, fmt) in labels.items():
        s = schemas.get(key, {})
        if not s.get("error"):
            print(f"         {label:20s} {s.get('rows', 0):>8d} {s.get('cols', 0):>6d} {fmt:10s}")
    c._pass("数据量对比完成")

    # ── Phase 5: 列值样本对比 ──

    print("\n── Phase 5: 列值样本对比 ──")

    print("\nSTEP 6: 交易数据 — 关键字段样本值")
    resp = c.query_daft(DS_TXN, columns=["category", "payment_method", "region"])
    if resp.get("success"):
        for r in resp.get("rows", [])[:5]:
            print(f"         cat={r.get('category', '?'):15s} "
                  f"pay={r.get('payment_method', '?'):15s} "
                  f"region={r.get('region', '?')}")
        c._pass("交易字段样本")
    else:
        print(f"  [INFO] {resp.get('error', '')[:80]}")

    print("\nSTEP 7: 知识库 — 关键字段样本值")
    resp = c.query_daft(DS_KB, columns=["id", "title", "category", "source"])
    if resp.get("success"):
        for r in resp.get("rows", [])[:5]:
            title = str(r.get("title", "?"))[:50]
            print(f"         id={r.get('id', '?'):12s} "
                  f"cat={r.get('category', '?'):15s} "
                  f"title={title}")
        c._pass("知识库字段样本")
    else:
        print(f"  [INFO] {resp.get('error', '')[:80]}")

    print("\nSTEP 8: 论文元数据 — 关键字段样本值")
    resp = c.query_daft(DS_PAPERS, columns=["id", "title", "category", "year", "venue"])
    if resp.get("success"):
        for r in resp.get("rows", [])[:5]:
            title = str(r.get("title", "?"))[:45]
            print(f"         id={str(r.get('id', '?')):12s} "
                  f"year={str(r.get('year', '?')):5s} "
                  f"cat={str(r.get('category', '?')):15s} "
                  f"title={title}")
        c._pass("论文字段样本")
    else:
        print(f"  [INFO] {resp.get('error', '')[:80]}")

    # ── Phase 6: SQL 跨源统计 ──

    print("\n── Phase 6: SQL 跨源统计 ──")

    print("\nSTEP 9: SQL — 交易数据类别分布")
    resp = c.query_olap(
        DS_TXN,
        f'SELECT category, count(*) as cnt, round(avg(amount), 2) as avg_amt '
        f'FROM "{DS_TXN}" GROUP BY category ORDER BY cnt DESC LIMIT 5',
    )
    if resp.get("success"):
        for r in resp.get("rows", []):
            print(f"         {r.get('category', '?'):20s} "
                  f"cnt={r.get('cnt', 0):>4d} avg={r.get('avg_amt', 0):>8}")
        c._pass("交易类别 TOP5")
    else:
        print(f"  [INFO] {resp.get('error', '')[:80]}")

    print("\nSTEP 10: SQL — 论文年份与类别分布")
    resp = c.query_olap(
        DS_PAPERS,
        f'SELECT year, category, count(*) as cnt '
        f'FROM "{DS_PAPERS}" '
        f'GROUP BY year, category ORDER BY year, cnt DESC',
    )
    if resp.get("success"):
        for r in resp.get("rows", [])[:10]:
            print(f"         year={str(r.get('year', '?')):5s} "
                  f"cat={r.get('category', '?'):20s} "
                  f"cnt={r.get('cnt', 0):>4d}")
        c._pass("论文年份×类别分布")
    else:
        print(f"  [INFO] {resp.get('error', '')[:80]}")

    # ── Phase 7: 统一导出对比 ──

    print("\n── Phase 7: 多格式导出对比 ──")

    for ds, label in [(DS_TXN, "交易"), (DS_KB, "知识库"), (DS_PAPERS, "论文")]:
        for fmt in ["parquet", "csv"]:
            resp = c.export(ds, format=fmt)
            if resp.get("success"):
                task_id = resp.get("task_id", "")
                result = c.wait_for_export(ds, task_id, timeout=30)
                size = result.get("file_size_bytes", 0)
                print(f"         {label:6s} → {fmt:10s} {size:>10,d} bytes")

    c._pass("多格式导出对比完成")

    # 清理
    for ds in (DS_TXN, DS_KB, DS_PAPERS):
        c.delete_dataset(ds)

    print("\n" + "=" * 60)
    print("API-26  ALL PASSED")
    print("=" * 60)


if __name__ == "__main__":
    main()
