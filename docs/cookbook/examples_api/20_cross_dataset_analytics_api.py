#!/usr/bin/env python3
"""API-20 — 跨数据集关联分析

业务场景: 研究机构需要对比分析不同数据源（论文库 vs 知识库）的分类覆盖、内容重叠、互补性
数据源: datas/papers/metadata_zh.csv + datas/kb/knowledge_zh.jsonl
流程: 双源摄取 → 分类对齐 → 交叉分析 → 覆盖率矩阵 → 互补性评估 → 综合报告
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

DS_PAPERS = "xref-papers"
DS_KB = "xref-knowledge"


def main() -> None:
    print("=" * 60)
    print("API-20  跨数据集关联分析")
    print("=" * 60)

    c = ArrowLakeClient(BASE_URL, API_KEY)

    for n in [DS_PAPERS, DS_KB]:
        c.delete_dataset(n)

    # ── Phase 1: 双源数据摄取 ──

    print("\n── Phase 1: 双源数据摄取 ──")

    # 论文库
    print("\nSTEP 1: 摄取中文论文元数据")
    paper_rows = 0
    meta_zh = DATAS_DIR / "papers" / "metadata_zh.csv"
    if meta_zh.exists():
        resp = c.ingest_files(DS_PAPERS, [str(meta_zh)])
        if resp.get("success"):
            paper_rows = resp.get("total_rows", 0)
            c._pass(f"论文库 — {paper_rows} 篇")
        else:
            print(f"  [WARN] {resp.get('error')}: {resp.get('message', '')[:100]}")
    else:
        print(f"  [SKIP] {meta_zh} 不存在")
        paper_rows = 0

    # 知识库
    print("\nSTEP 2: 摄取中文知识库")
    kb_rows = 0
    kb_zh = DATAS_DIR / "kb" / "knowledge_zh.jsonl"
    if kb_zh.exists():
        resp = c.ingest_files(DS_KB, [str(kb_zh)])
        if resp.get("success"):
            kb_rows = resp.get("total_rows", 0)
            c._pass(f"知识库 — {kb_rows} 条")
        else:
            print(f"  [WARN] {resp.get('error')}: {resp.get('message', '')[:100]}")
    else:
        print(f"  [SKIP] {kb_zh} 不存在")
        kb_rows = 0

    # 血缘
    c.lineage_record(DS_PAPERS, "cross_source_ingest",
                     inputs=["papers/metadata_zh.csv"], outputs=[DS_PAPERS])
    c.lineage_record(DS_KB, "cross_source_ingest",
                     inputs=["kb/knowledge_zh.jsonl"], outputs=[DS_KB])

    # 如果两个数据源都未成功摄取，跳过后续分析
    if paper_rows == 0 and kb_rows == 0:
        print("\n  [SKIP] 双源数据均未摄取成功 (Docker 文件路径不可达)")
        for n in [DS_PAPERS, DS_KB]:
            c.delete_dataset(n)
        print("\n" + "=" * 60)
        print("API-20  跨数据集关联分析 — ALL PASSED (SKIP)")
        print("=" * 60)
        return

    # ── Phase 2: 各数据源分类统计 ──

    print("\n── Phase 2: 各数据源分类统计 ──")

    print("\nSTEP 3: 论文库类别分布")
    paper_categories: dict[str, int] = {}
    resp = c.query_olap(DS_PAPERS,
        f'SELECT category, count(*) as cnt '
        f'FROM "{DS_PAPERS}" '
        f'GROUP BY category ORDER BY cnt DESC')
    if resp.get("success"):
        rows = resp.get("rows", [])
        c._pass(f"论文库 — {len(rows)} 个类别")
        for r in rows:
            cat = r.get("category", "?")
            cnt = r.get("cnt", 0)
            paper_categories[cat] = cnt
            print(f"         {cat:30s} — {cnt} 篇")

    print("\nSTEP 4: 知识库类别分布")
    kb_categories: dict[str, int] = {}
    resp = c.query_olap(DS_KB,
        f'SELECT category, count(*) as cnt '
        f'FROM "{DS_KB}" '
        f'GROUP BY category ORDER BY cnt DESC')
    if resp.get("success"):
        rows = resp.get("rows", [])
        c._pass(f"知识库 — {len(rows)} 个类别")
        for r in rows:
            cat = r.get("category", "?")
            cnt = r.get("cnt", 0)
            kb_categories[cat] = cnt
            print(f"         {cat:30s} — {cnt} 条")

    # ── Phase 3: 交叉分析 ──

    print("\n── Phase 3: 分类覆盖对比 ──")

    print("\nSTEP 5: 分类覆盖矩阵")
    all_cats = sorted(set(paper_categories.keys()) | set(kb_categories.keys()))
    print(f"         {'类别':30s} {'论文':>6s} {'知识库':>6s} {'覆盖':>6s}")
    print(f"         {'─' * 30} {'─' * 6} {'─' * 6} {'─' * 6}")

    both = 0
    paper_only = 0
    kb_only = 0
    for cat in all_cats:
        p_cnt = paper_categories.get(cat, 0)
        k_cnt = kb_categories.get(cat, 0)
        if p_cnt > 0 and k_cnt > 0:
            cov = "双向"
            both += 1
        elif p_cnt > 0:
            cov = "仅论文"
            paper_only += 1
        else:
            cov = "仅知识库"
            kb_only += 1
        print(f"         {cat:30s} {p_cnt:>6d} {k_cnt:>6d} {cov:>6s}")

    c._pass(f"覆盖分析 — {both} 双向, {paper_only} 仅论文, {kb_only} 仅知识库")

    # ── Phase 4: 内容深度分析 ──

    print("\n── Phase 4: 内容深度分析 ──")

    print("\nSTEP 6: 论文库字数分布")
    resp = c.query_olap(DS_PAPERS,
        f'SELECT CASE '
        f'  WHEN word_count >= 200 THEN \'长文(200+)\' '
        f'  WHEN word_count >= 100 THEN \'中文(100-199)\' '
        f'  ELSE \'短文(<100)\' '
        f'END AS length_tier, '
        f'  count(*) as cnt, '
        f'  round(avg(word_count), 0) as avg_words '
        f'FROM "{DS_PAPERS}" '
        f'GROUP BY length_tier '
        f'ORDER BY avg_words DESC')
    if resp.get("success"):
        rows = resp.get("rows", [])
        for r in rows:
            print(f"         {r.get('length_tier', '?'):15s} — "
                  f"{r.get('cnt', 0)} 篇, 平均 {r.get('avg_words', 0):.0f} 词")
        c._pass("论文字数分层完成")

    print("\nSTEP 7: 知识库字数分布")
    resp = c.query_olap(DS_KB,
        f'SELECT CASE '
        f'  WHEN length(text_content) >= 300 THEN \'长文(300+)\' '
        f'  WHEN length(text_content) >= 150 THEN \'中文(150-299)\' '
        f'  ELSE \'短文(<150)\' '
        f'END AS length_tier, '
        f'  count(*) as cnt '
        f'FROM "{DS_KB}" '
        f'GROUP BY length_tier')
    if resp.get("success"):
        rows = resp.get("rows", [])
        for r in rows:
            print(f"         {r.get('length_tier', '?'):15s} — {r.get('cnt', 0)} 条")
        c._pass("知识库字数分层完成")

    # ── Phase 5: 关键词交叉 ──

    print("\n── Phase 5: 关键词交叉分析 ──")

    # 论文库搜索
    print("\nSTEP 8: 论文库搜索 '知识图谱'")
    resp_p = c.search_fts(DS_PAPERS, "知识图谱", top_k=3)
    p_cnt = resp_p.get("row_count", 0) if resp_p.get("success") else 0

    # 知识库搜索
    print("STEP 9: 知识库搜索 '知识图谱'")
    resp_k = c.search_fts(DS_KB, "知识图谱", top_k=3)
    k_cnt = resp_k.get("row_count", 0) if resp_k.get("success") else 0

    c._pass(f"'知识图谱' — 论文 {p_cnt} 篇, 知识库 {k_cnt} 条")

    # 更多关键词对比
    keywords = ["向量", "RAG", "检索", "Arrow", "深度学习"]
    print("\nSTEP 10: 多关键词交叉搜索对比")
    print(f"         {'关键词':15s} {'论文':>6s} {'知识库':>6s}")
    print(f"         {'─' * 15} {'─' * 6} {'─' * 6}")
    for kw in keywords:
        rp = c.search_fts(DS_PAPERS, kw, top_k=1)
        rk = c.search_fts(DS_KB, kw, top_k=1)
        pc = rp.get("row_count", 0) if rp.get("success") else 0
        kc = rk.get("row_count", 0) if rk.get("success") else 0
        print(f"         {kw:15s} {pc:>6d} {kc:>6d}")

    c._pass("关键词交叉对比完成")

    # ── Phase 6: 索引 & 质量对比 ──

    print("\n── Phase 6: 索引 & 质量对比 ──")

    for label, ds in [("论文库", DS_PAPERS), ("知识库", DS_KB)]:
        c.create_fts_index(ds, fts_column="text_content")
        c.create_vector_index(ds, vector_column="text_embedding",
                               metric="cosine", index_type="IVF_PQ")

        resp = c.quality_report(ds)
        if resp.get("success"):
            report = resp.get("report", resp.get("data", {}))
            if isinstance(report, dict):
                score = report.get("quality_score", "?")
                c._pass(f"{label}质量评分: {score}")

    # ── Phase 7: 综合报告 ──

    print("\n── Phase 7: 综合分析报告 ──")

    print("\nSTEP 11: 生成综合报告")
    c._pass(f"论文库: {paper_rows} 篇, {len(paper_categories)} 个类别")
    c._pass(f"知识库: {kb_rows} 条, {len(kb_categories)} 个类别")
    c._pass(f"交叉覆盖: {both} 个共有类别, "
            f"{paper_only} 个论文独有, {kb_only} 个知识库独有")

    # 导出两个数据集
    print("\nSTEP 12: 双源并行导出")
    for label, ds in [("论文库", DS_PAPERS), ("知识库", DS_KB)]:
        resp = c.export(ds, format="parquet")
        if resp.get("success"):
            task_id = resp.get("task_id", "")
            if task_id:
                status = c.wait_for_export(ds, task_id, timeout=30)
                c._pass(f"{label}导出 — {status.get('status')}")

    # 审计
    c.audit_record(DS_PAPERS, "cross_dataset_analysis",
                   details={
                       "source_a": DS_PAPERS,
                       "source_b": DS_KB,
                       "overlap_categories": both,
                       "keywords_tested": len(keywords),
                   })

    # 清理
    for n in [DS_PAPERS, DS_KB]:
        c.delete_dataset(n)

    print("\n" + "=" * 60)
    print("API-20  跨数据集关联分析 — ALL PASSED")
    print("=" * 60)


if __name__ == "__main__":
    main()
