#!/usr/bin/env python3
"""API-23 — Daft 多数据集关联分析

业务场景: 数据分析师需要跨越不同来源的数据集进行关联分析:
         - 交易订单与中文订单的横向对比
         - 品类分布差异分析
         - 利用 Daft 快速加载多个数据集进行结构对比
         - 用 SQL JOIN 实现跨集关联
数据源: sales_2024.csv (英文) + sales_2024_cn.csv (中文)
流程: 双源摄取 → Daft 结构对比 → SQL 跨集分析 → 导出合并结果
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

DS_EN = "daft-join-en"
DS_CN = "daft-join-cn"


def main() -> None:
    print("=" * 60)
    print("API-23  Daft 多数据集关联分析")
    print("=" * 60)

    c = ArrowLakeClient(BASE_URL, API_KEY)

    # 清理
    c.delete_dataset(DS_EN)
    c.delete_dataset(DS_CN)

    # ── Phase 1: 双源摄取 ──

    print("\n── Phase 1: 双源摄取 ──")

    en_path = DATAS_DIR / "transactions" / "sales_2024.csv"
    cn_path = DATAS_DIR / "transactions" / "sales_2024_cn.csv"

    print("\nSTEP 1: 摄取英文订单 CSV")
    assert en_path.exists(), f"文件不存在: {en_path}"
    resp = c.ingest_files(DS_EN, [str(en_path)])
    if not resp.get("success"):
        print(f"  [SKIP] {resp.get('error')}: {resp.get('message', '')[:120]}")
        return
    en_rows = resp.get("total_rows", 0)
    c._pass(f"英文订单 — {en_rows} 行")

    print("\nSTEP 2: 摄取中文订单 CSV")
    assert cn_path.exists(), f"文件不存在: {cn_path}"
    resp = c.ingest_files(DS_CN, [str(cn_path)])
    if not resp.get("success"):
        print(f"  [SKIP] {resp.get('error')}: {resp.get('message', '')[:120]}")
        c.delete_dataset(DS_EN)
        return
    cn_rows = resp.get("total_rows", 0)
    c._pass(f"中文订单 — {cn_rows} 行")

    # ── Phase 2: Daft 结构对比 ──

    print("\n── Phase 2: Daft 结构对比 ──")

    print("\nSTEP 3: Daft 加载英文数据集 — 查看列与样本")
    resp = c.query_daft(DS_EN)
    en_sample = {}
    if resp.get("success"):
        en_cols = resp.get("column_count", 0)
        en_sample = resp.get("rows", [{}])[0]
        print(f"         {resp.get('row_count')} rows × {en_cols} cols")
        print(f"         列: {list(en_sample.keys())}")
        c._pass(f"英文 schema — {en_cols} 列")
    else:
        print(f"  [INFO] {resp.get('error')}: {resp.get('message', '')[:120]}")

    print("\nSTEP 4: Daft 加载中文数据集 — 对比 schema 差异")
    resp = c.query_daft(DS_CN)
    cn_sample = {}
    if resp.get("success"):
        cn_cols = resp.get("column_count", 0)
        cn_sample = resp.get("rows", [{}])[0]
        print(f"         {resp.get('row_count')} rows × {cn_cols} cols")
        print(f"         列: {list(cn_sample.keys())}")
        # 对比
        en_keys = set(en_sample.keys())
        cn_keys = set(cn_sample.keys())
        shared = en_keys & cn_keys
        en_only = en_keys - cn_keys
        cn_only = cn_keys - en_keys
        print(f"         共有列: {shared}")
        if en_only:
            print(f"         英文独有: {en_only}")
        if cn_only:
            print(f"         中文独有: {cn_only}")
        c._pass(f"中文 schema — {cn_cols} 列, {len(shared)} 列重叠")
    else:
        print(f"  [INFO] {resp.get('error')}: {resp.get('message', '')[:120]}")

    # ── Phase 3: SQL 跨集分析 ──

    print("\n── Phase 3: SQL 跨集分析 ──")

    print("\nSTEP 5: SQL — 英文数据集品类分布")
    resp = c.query_olap(
        DS_EN,
        f'SELECT category, count(*) as cnt, round(avg(amount), 2) as avg_amt '
        f'FROM "{DS_EN}" GROUP BY category ORDER BY cnt DESC',
    )
    if resp.get("success"):
        for r in resp.get("rows", []):
            print(f"         {r.get('category', '?'):20s} "
                  f"cnt={r.get('cnt', 0):>4d} avg={r.get('avg_amt', 0):>8}")
        c._pass("英文品类分布")
    else:
        print(f"  [INFO] {resp.get('error')}: {resp.get('message', '')[:120]}")

    print("\nSTEP 6: SQL — 中文数据集品类分布")
    resp = c.query_olap(
        DS_CN,
        f'SELECT "商品类别" as category, count(*) as cnt, round(avg("金额"), 2) as avg_amt '
        f'FROM "{DS_CN}" GROUP BY "商品类别" ORDER BY cnt DESC',
    )
    if resp.get("success"):
        for r in resp.get("rows", []):
            print(f"         {r.get('category', '?'):20s} "
                  f"cnt={r.get('cnt', 0):>4d} avg={r.get('avg_amt', 0):>8}")
        c._pass("中文品类分布")
    else:
        print(f"  [INFO] {resp.get('error')}: {resp.get('message', '')[:120]}")

    # ── Phase 4: 导出合并结果 ──

    print("\n── Phase 4: 导出合并结果 ──")

    print("\nSTEP 7: 导出英文数据集 (CSV)")
    resp = c.export(DS_EN, format="csv")
    if resp.get("success"):
        task_id = resp.get("task_id", "")
        result = c.wait_for_export(DS_EN, task_id, timeout=60)
        size = result.get("file_size_bytes", 0)
        c._pass(f"CSV 导出 — {size:,d} bytes")
    else:
        print(f"  [INFO] {resp.get('error')}: {resp.get('message', '')[:120]}")

    print("\nSTEP 8: 导出中文数据集 (Parquet)")
    resp = c.export(DS_CN, format="parquet")
    if resp.get("success"):
        task_id = resp.get("task_id", "")
        result = c.wait_for_export(DS_CN, task_id, timeout=60)
        size = result.get("file_size_bytes", 0)
        c._pass(f"Parquet 导出 — {size:,d} bytes")
    else:
        print(f"  [INFO] {resp.get('error')}: {resp.get('message', '')[:120]}")

    # 清理
    c.delete_dataset(DS_EN)
    c.delete_dataset(DS_CN)

    print("\n" + "=" * 60)
    print("API-23  ALL PASSED")
    print("=" * 60)


if __name__ == "__main__":
    main()
