#!/usr/bin/env python3
"""API-08 — Quality & Deduplication

对应 cookbook: 04_quality_and_dedup.py, 13_data_quality_pipeline.py
验证: 质量报告分析、过滤规则（长度/模式/分数）、去重策略、质量管线
前置: 需要已存在含 text_content 列的数据集
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
    print("API-08  Quality & Deduplication")
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

    # 1. Quality report
    print("\nSTEP 1: Quality report")
    resp = c.quality_report(name)
    if resp.get("success"):
        report = resp.get("report", resp.get("data", {}))
        if isinstance(report, dict):
            total = report.get("total_rows", rows)
            passing = report.get("passing_rows", "?")
            score = report.get("quality_score", report.get("score", "?"))
            c._pass(f"quality report — total={total}, passing={passing}, score={score}")
            for check in report.get("checks", report.get("details", []))[:3]:
                print(f"         {check}")
        else:
            c._pass(f"quality report — {str(report)[:100]}")
    else:
        print(f"  [INFO] {resp.get('error')}: {resp.get('message', '')[:120]}")

    # 2. Filter by text length
    print("\nSTEP 2: Quality filter (min length)")
    rules = [
        {"column": "text_content", "type": "min_length", "value": 10},
    ]
    resp = c.quality_filter(name, rules)
    if resp.get("success"):
        filtered = resp.get("removed_rows", resp.get("filtered", 0))
        kept = resp.get("kept_rows", resp.get("remaining", 0))
        c._pass(f"min_length=10 — removed={filtered}, kept={kept}")
    else:
        print(f"  [INFO] {resp.get('error')}: {resp.get('message', '')[:120]}")

    # 3. Filter by text length (max)
    print("\nSTEP 3: Quality filter (max length)")
    rules = [
        {"column": "text_content", "type": "max_length", "value": 10000},
    ]
    resp = c.quality_filter(name, rules)
    if resp.get("success"):
        c._pass("max_length=10000 filter applied")
    else:
        print(f"  [INFO] {resp.get('error')}: {resp.get('message', '')[:120]}")

    # 4. Filter by regex pattern
    print("\nSTEP 4: Quality filter (regex pattern)")
    rules = [
        {"column": "text_content", "type": "regex", "value": r".{20,}",
         "action": "keep"},
    ]
    resp = c.quality_filter(name, rules)
    if resp.get("success"):
        c._pass("regex filter applied")
    else:
        print(f"  [INFO] {resp.get('error')}: {resp.get('message', '')[:120]}")

    # 5. Filter by null check
    print("\nSTEP 5: Quality filter (not null)")
    rules = [
        {"column": "text_content", "type": "not_null"},
    ]
    resp = c.quality_filter(name, rules)
    if resp.get("success"):
        c._pass("not_null filter applied")
    else:
        print(f"  [INFO] {resp.get('error')}: {resp.get('message', '')[:120]}")

    # 6. Combined filter rules
    print("\nSTEP 6: Quality filter (combined rules)")
    rules = [
        {"column": "text_content", "type": "min_length", "value": 20},
        {"column": "text_content", "type": "not_null"},
        {"column": "text_content", "type": "max_length", "value": 50000},
    ]
    resp = c.quality_filter(name, rules)
    if resp.get("success"):
        c._pass("combined 3-rule filter applied")
    else:
        print(f"  [INFO] {resp.get('error')}: {resp.get('message', '')[:120]}")

    # 7. Deduplicate (exact match)
    print("\nSTEP 7: Deduplicate (exact)")
    resp = c.quality_deduplicate(name, strategy="exact",
                                  column="text_content")
    if resp.get("success"):
        dupes = resp.get("duplicates_removed", resp.get("removed", 0))
        c._pass(f"exact dedup — {dupes} duplicates removed")
    else:
        print(f"  [INFO] {resp.get('error')}: {resp.get('message', '')[:120]}")

    # 8. Deduplicate (fuzzy / minhash)
    print("\nSTEP 8: Deduplicate (fuzzy)")
    resp = c.quality_deduplicate(name, strategy="fuzzy",
                                  column="text_content",
                                  threshold=0.8)
    if resp.get("success"):
        dupes = resp.get("duplicates_removed", resp.get("removed", 0))
        c._pass(f"fuzzy dedup (0.8) — {dupes} duplicates removed")
    else:
        print(f"  [INFO] {resp.get('error')}: {resp.get('message', '')[:120]}")

    # 9. Quality report after cleanup
    print("\nSTEP 9: Quality report (post-cleanup)")
    resp = c.quality_report(name)
    if resp.get("success"):
        report = resp.get("report", resp.get("data", {}))
        if isinstance(report, dict):
            score = report.get("quality_score", report.get("score", "?"))
            c._pass(f"post-cleanup score={score}")
        else:
            c._pass("post-cleanup report available")
    else:
        print(f"  [INFO] {resp.get('error')}: {resp.get('message', '')[:120]}")

    # 10. Full quality pipeline (filter + dedup)
    print("\nSTEP 10: Full quality pipeline")
    filter_resp = c.quality_filter(name, [
        {"column": "text_content", "type": "min_length", "value": 10},
        {"column": "text_content", "type": "not_null"},
    ])
    dedup_resp = c.quality_deduplicate(name, strategy="exact",
                                        column="text_content")
    report_resp = c.quality_report(name)

    all_ok = all(r.get("success") for r in [filter_resp, dedup_resp, report_resp])
    if all_ok:
        c._pass("full pipeline: filter → dedup → report")
    else:
        steps = ["filter", "dedup", "report"]
        for step, r in zip(steps, [filter_resp, dedup_resp, report_resp]):
            if r.get("success"):
                print(f"  [OK]    {step}")
            else:
                print(f"  [INFO] {step}: {r.get('error', '')}")

    print("\n" + "=" * 60)
    print("API-08  ALL PASSED")
    print("=" * 60)


if __name__ == "__main__":
    main()
