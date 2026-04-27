#!/usr/bin/env python3
"""17 — 跨数据集关联分析

场景: 用 SQL JOIN 关联论文和知识库数据，分析分类交叉覆盖。

数据文件: datas/papers/metadata_zh.csv, datas/kb/knowledge_zh.jsonl
"""

from __future__ import annotations

import argparse

import shutil
import sys
from pathlib import Path

from arrow_lake import Lake

DATAS_DIR = Path(__file__).resolve().parent.parent / "datas"
_DEFAULT_BASE_URI = "./_tmp_multi_join"


def main() -> None:
    parser = argparse.ArgumentParser(description="17_multi_dataset_join.py")
    parser.add_argument("--base-uri", default=_DEFAULT_BASE_URI)
    parser.add_argument("--no-cleanup", action="store_true")
    args = parser.parse_args()
    no_cleanup = args.no_cleanup
    print("=" * 60)
    print("17 跨数据集关联分析")
    print("=" * 60)

    base = Path(args.base_uri)
    if base.exists():
        shutil.rmtree(base)

    lake = Lake(base_uri=args.base_uri)

    # STEP 1: 分别摄取
    print("STEP 1: 摄取论文 + 知识库")
    r1 = lake.ingest("papers_zh", [str(DATAS_DIR / "papers" / "metadata_zh.csv")])
    r2 = lake.ingest("knowledge_zh", [str(DATAS_DIR / "kb" / "knowledge_zh.jsonl")])
    print(f"  论文: {r1.total_rows} 行, 知识库: {r2.total_rows} 行")

    # STEP 2: 各自分类统计
    print("\nSTEP 2: 各数据集分类统计")
    for ds_name in ["papers_zh", "knowledge_zh"]:
        result = lake.olap_query(ds_name,
            f"SELECT category, COUNT(*) as cnt FROM {ds_name} GROUP BY category ORDER BY cnt DESC")
        print(f"  [{ds_name}]")
        for row in result.table.to_pylist():
            print(f"    {row['category']:<16} {row['cnt']:>3} 条")
        print()

    # STEP 3: 交叉分类覆盖分析
    print("STEP 3: 交叉分类覆盖分析")
    result = lake.olap_query("papers_zh",
        "SELECT p.category, COUNT(*) as paper_cnt FROM papers_zh p GROUP BY p.category ORDER BY p.category")
    paper_cats = {row["category"]: row["paper_cnt"] for row in result.table.to_pylist()}

    result = lake.olap_query("knowledge_zh",
        "SELECT k.category, COUNT(*) as kb_cnt FROM knowledge_zh k GROUP BY k.category ORDER BY k.category")
    kb_cats = {row["category"]: row["kb_cnt"] for row in result.table.to_pylist()}

    all_cats = sorted(set(paper_cats.keys()) | set(kb_cats.keys()))
    print(f"  {'分类':<16} {'论文':>5} {'知识库':>5} {'状态':>8}")
    print(f"  {'-'*16} {'-'*5} {'-'*5} {'-'*8}")
    for cat in all_cats:
        pc = paper_cats.get(cat, 0)
        kc = kb_cats.get(cat, 0)
        if pc > 0 and kc > 0:
            status = "交叉"
        elif pc > 0:
            status = "仅论文"
        else:
            status = "仅知识库"
        print(f"  {cat:<16} {pc:>5} {kc:>5} {status:>8}")

    # STEP 4: 论文字数分布
    print("\nSTEP 4: 论文字数分布")
    result = lake.olap_query("papers_zh",
        "SELECT CASE "
        "  WHEN word_count < 3000 THEN '短 (<3k)' "
        "  WHEN word_count < 6000 THEN '中 (3k-6k)' "
        "  ELSE '长 (6k+)' "
        "END as 长度段, COUNT(*) as cnt, ROUND(AVG(word_count),0) as avg_words "
        "FROM papers_zh GROUP BY 1 ORDER BY avg_words")
    for row in result.table.to_pylist():
        print(f"  {row['长度段']:<16} {row['cnt']:>3} 篇  均字数 {row['avg_words']:.0f}")

    # STEP 5: 导出关联分析
    print("\nSTEP 5: 导出各数据集")
    for ds_name in ["papers_zh", "knowledge_zh"]:
        out = base / f"{ds_name}_analysis.parquet"
        lake.export(ds_name, str(out), format="parquet")
        print(f"  {ds_name} → {out.name} ({out.stat().st_size // 1024} KB)")

    print("\n  [全部 PASS]")
    if not no_cleanup:
        lake.shutdown()
        shutil.rmtree(base, ignore_errors=True)
        print("(已清理)")


if __name__ == "__main__":
    main()
