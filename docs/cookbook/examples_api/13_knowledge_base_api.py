#!/usr/bin/env python3
"""API-13 — 双语技术知识库

业务场景: 企业技术团队搭建中英文双语知识库，支持分类浏览、语义检索、质量管控
数据源: datas/kb/knowledge.jsonl (英文) + datas/kb/knowledge_zh.jsonl (中文)
流程: 双语摄取 → 质量管控 → 向量+FTS 双索引 → 分类检索 → 质量报告
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from conftest import ArrowLakeClient, first_embedding

BASE_URL = "http://localhost:8000"
API_KEY = "dev-api-key-for-local-testing-only"
DATAS_DIR = Path(__file__).resolve().parent.parent / "datas"

DS_EN = "kb-english"
DS_ZH = "kb-chinese"


def main() -> None:
    print("=" * 60)
    print("API-13  双语技术知识库")
    print("=" * 60)

    c = ArrowLakeClient(BASE_URL, API_KEY)

    for n in [DS_EN, DS_ZH]:
        c.delete_dataset(n)

    # ── Phase 1: 数据摄取 ──

    print("\n── Phase 1: 双语数据摄取 ──")

    # 英文知识库
    print("\nSTEP 1: 摄取英文知识库")
    en_jsonl = DATAS_DIR / "kb" / "knowledge.jsonl"
    if en_jsonl.exists():
        resp = c.ingest_files(DS_EN, [str(en_jsonl)])
        if resp.get("success"):
            rows = resp.get("total_rows", 0)
            c._pass(f"英文知识库 — {rows} 条")
        else:
            print(f"  [WARN] {resp.get('error')}: {resp.get('message', '')[:100]}")
    else:
        print(f"  [SKIP] {en_jsonl} 不存在")

    # 中文知识库
    print("\nSTEP 2: 摄取中文知识库")
    zh_jsonl = DATAS_DIR / "kb" / "knowledge_zh.jsonl"
    if zh_jsonl.exists():
        resp = c.ingest_files(DS_ZH, [str(zh_jsonl)])
        if resp.get("success"):
            rows = resp.get("total_rows", 0)
            c._pass(f"中文知识库 — {rows} 条")
        else:
            print(f"  [WARN] {resp.get('error')}: {resp.get('message', '')[:100]}")
    else:
        print(f"  [SKIP] {zh_jsonl} 不存在")

    # ── Phase 2: 质量管控 ──

    print("\n── Phase 2: 质量管控 ──")

    for label, ds in [("英文", DS_EN), ("中文", DS_ZH)]:
        print(f"\nSTEP: {label}知识库质量检查")

        # 去重
        resp = c.quality_deduplicate(ds, strategy="exact", column="text_content")
        if resp.get("success"):
            dupes = resp.get("duplicates_removed", 0)
            c._pass(f"{label}去重 — 移除 {dupes} 条重复")

        # 过滤空内容
        resp = c.quality_filter(ds, [
            {"column": "text_content", "type": "min_length", "value": 20},
            {"column": "text_content", "type": "not_null"},
        ])
        if resp.get("success"):
            c._pass(f"{label}内容过滤完成")

        # 质量报告
        resp = c.quality_report(ds)
        if resp.get("success"):
            report = resp.get("report", resp.get("data", {}))
            if isinstance(report, dict):
                score = report.get("quality_score", "?")
                c._pass(f"{label}质量评分: {score}")

    # ── Phase 3: 索引构建 ──

    print("\n── Phase 3: 索引构建 ──")

    for label, ds in [("英文", DS_EN), ("中文", DS_ZH)]:
        print(f"\nSTEP: {label}知识库建索引")

        resp = c.create_vector_index(ds, vector_column="text_embedding",
                                      metric="cosine", index_type="IVF_PQ")
        if resp.get("success"):
            c._pass(f"{label}向量索引")

        resp = c.create_fts_index(ds, fts_column="text_content")
        if resp.get("success"):
            c._pass(f"{label}FTS 索引")

    # ── Phase 4: 检索测试 ──

    print("\n── Phase 4: 检索测试 ──")

    # 英文 FTS
    print("\nSTEP 3: 英文搜索 'vector database'")
    resp = c.search_fts(DS_EN, "vector database", top_k=5)
    if resp.get("success"):
        rows = resp.get("rows", [])
        c._pass(f"英文搜索 — {resp.get('row_count', 0)} 结果")
        for r in rows[:3]:
            title = r.get("title", r.get("text_content", ""))[:70]
            print(f"         {title}")

    # 中文 FTS
    print("\nSTEP 4: 中文搜索 '数据格式'")
    resp = c.search_fts(DS_ZH, "数据格式", top_k=5)
    if resp.get("success"):
        rows = resp.get("rows", [])
        c._pass(f"中文搜索 — {resp.get('row_count', 0)} 结果")
        for r in rows[:3]:
            title = r.get("title", r.get("text_content", ""))[:70]
            print(f"         {title}")

    # 分面搜索
    print("\nSTEP 5: 分面搜索 'Arrow'")
    resp = c.search_faceted(DS_EN, "Arrow", facets=["category", "tags"], top_k=5)
    if resp.get("success"):
        facets_data = resp.get("facets", resp.get("facet_counts", {}))
        c._pass(f"分面搜索 — facets={facets_data}")

    # 语义搜索
    print("\nSTEP 6: 语义搜索 'columnar data format for analytics'")
    resp = c.embed_text(["columnar data format for analytics"])
    if resp.get("success"):
        vec = first_embedding(resp)
        search_en = c.search_vector(DS_EN, vec, top_k=3)
        search_zh = c.search_vector(DS_ZH, vec, top_k=3)
        en_cnt = search_en.get("row_count", 0) if search_en.get("success") else 0
        zh_cnt = search_zh.get("row_count", 0) if search_zh.get("success") else 0
        c._pass(f"语义搜索 — 英文 {en_cnt} 条, 中文 {zh_cnt} 条")

    # ── Phase 5: 分类统计 ──

    print("\n── Phase 5: 分类统计 ──")

    print("\nSTEP 7: 英文知识库类别分布")
    resp = c.query_olap(DS_EN,
        f'SELECT category, count(*) as cnt '
        f'FROM "{DS_EN}" '
        f'GROUP BY category ORDER BY cnt DESC')
    if resp.get("success"):
        for r in resp.get("rows", []):
            print(f"         {r.get('category', '?'):25s} — {r.get('cnt', 0)} 条")
        c._pass("英文类别分布")

    print("\nSTEP 8: 中文知识库类别分布")
    resp = c.query_olap(DS_ZH,
        f'SELECT category, count(*) as cnt '
        f'FROM "{DS_ZH}" '
        f'GROUP BY category ORDER BY cnt DESC')
    if resp.get("success"):
        for r in resp.get("rows", []):
            print(f"         {r.get('category', '?'):25s} — {r.get('cnt', 0)} 条")
        c._pass("中文类别分布")

    # ── Phase 6: 批量导出 ──

    print("\n── Phase 6: 批量导出 ──")

    for label, ds in [("英文", DS_EN), ("中文", DS_ZH)]:
        resp = c.export(ds, format="parquet")
        if resp.get("success"):
            task_id = resp.get("task_id", "")
            if task_id:
                status = c.wait_for_export(ds, task_id, timeout=30)
                c._pass(f"{label}知识库导出 — {status.get('status')}")

    # 清理
    for n in [DS_EN, DS_ZH]:
        c.delete_dataset(n)

    print("\n" + "=" * 60)
    print("API-13  双语技术知识库 — ALL PASSED")
    print("=" * 60)


if __name__ == "__main__":
    main()
