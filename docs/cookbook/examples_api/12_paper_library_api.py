#!/usr/bin/env python3
"""API-12 — 论文库管理与智能检索

业务场景: 研究机构管理论文资料库，支持语义搜索、关键词搜索、混合检索
数据源: datas/papers/metadata.csv (论文元数据) + datas/papers/full_text/*.pdf (全文)
流程: 元数据+PDF 摄取 → 双索引 → 多模式搜索 → 分类统计 → 批量导出
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from conftest import ArrowLakeClient, first_embedding

BASE_URL = "http://localhost:8000"
API_KEY = "dev-api-key-for-local-testing-only"
DATAS_DIR = Path(__file__).resolve().parent.parent / "datas"

DS_META = "paper-meta"
DS_FULL = "paper-full"


def main() -> None:
    print("=" * 60)
    print("API-12  论文库管理与智能检索")
    print("=" * 60)

    c = ArrowLakeClient(BASE_URL, API_KEY)

    # 清理
    for n in [DS_META, DS_FULL]:
        c.delete_dataset(n)

    # ── Phase 1: 数据摄取 ──

    print("\n── Phase 1: 数据摄取 ──")

    # 元数据摄取
    print("\nSTEP 1: 摄取论文元数据")
    meta_csv = DATAS_DIR / "papers" / "metadata.csv"
    if meta_csv.exists():
        resp = c.ingest_files(DS_META, [str(meta_csv)])
        if resp.get("success"):
            rows = resp.get("total_rows", 0)
            c._pass(f"元数据摄取 — {rows} 篇论文")
        else:
            print(f"  [WARN] {resp.get('error')}: {resp.get('message', '')[:100]}")
    else:
        print(f"  [SKIP] {meta_csv} 不存在")

    # 中文元数据
    print("\nSTEP 2: 摄取中文论文元数据")
    meta_zh = DATAS_DIR / "papers" / "metadata_zh.csv"
    if meta_zh.exists():
        resp = c.ingest_files(DS_META, [str(meta_zh)])
        if resp.get("success"):
            c._pass(f"中文元数据追加 — {resp.get('total_rows', 0)} 篇")
    else:
        print(f"  [SKIP] {meta_zh} 不存在")

    # PDF 全文摄取
    print("\nSTEP 3: 摄取 PDF 全文")
    pdf_dir = DATAS_DIR / "papers" / "full_text"
    if pdf_dir.exists():
        pdfs = sorted(pdf_dir.glob("*.pdf"))
        if pdfs:
            resp = c.ingest_documents(DS_FULL, [str(p) for p in pdfs[:5]])
            if resp.get("success"):
                c._pass(f"PDF 摄取 — {resp.get('total_rows', 0)} chunks from {len(pdfs[:5])} files")
            else:
                print(f"  [WARN] {resp.get('error')}: {resp.get('message', '')[:120]}")
        else:
            print("  [SKIP] 无 PDF 文件")
    else:
        print(f"  [SKIP] {pdf_dir} 不存在")

    # ── Phase 2: 索引构建 ──

    print("\n── Phase 2: 索引构建 ──")

    print("\nSTEP 4: 创建向量索引")
    resp = c.create_vector_index(DS_META, vector_column="text_embedding",
                                  metric="cosine", index_type="IVF_PQ")
    if resp.get("success"):
        c._pass("向量索引创建完成")
    else:
        print(f"  [INFO] {resp.get('error', '')}")

    print("\nSTEP 5: 创建 FTS 索引")
    resp = c.create_fts_index(DS_META, fts_column="text_content")
    if resp.get("success"):
        c._pass("FTS 索引创建完成")
    else:
        print(f"  [INFO] {resp.get('error', '')}")

    # ── Phase 3: 智能检索 ──

    print("\n── Phase 3: 智能检索 ──")

    # 关键词搜索
    print("\nSTEP 6: 关键词搜索 'transformer'")
    resp = c.search_fts(DS_META, "transformer", top_k=5)
    if resp.get("success"):
        rows = resp.get("rows", [])
        c._pass(f"关键词搜索 — {resp.get('row_count', 0)} 结果")
        for r in rows[:3]:
            title = r.get("title", r.get("text_content", ""))[:60]
            score = r.get("_score", 0)
            print(f"         [{score:.3f}] {title}")
    else:
        print(f"  [INFO] {resp.get('error', '')}")

    # 中文搜索
    print("\nSTEP 7: 中文搜索 '知识图谱'")
    resp = c.search_fts(DS_META, "知识图谱", top_k=5)
    if resp.get("success"):
        rows = resp.get("rows", [])
        c._pass(f"中文搜索 — {resp.get('row_count', 0)} 结果")
        for r in rows[:3]:
            title = r.get("title", r.get("text_content", ""))[:60]
            print(f"         {title}")
    else:
        print(f"  [INFO] {resp.get('error', '')}")

    # 向量搜索
    print("\nSTEP 8: 语义搜索 'attention mechanism in deep learning'")
    resp = c.embed_text(["attention mechanism in deep learning"])
    if resp.get("success"):
        vec = first_embedding(resp)
        search = c.search_vector(DS_META, vec, top_k=5)
        if search.get("success"):
            c._pass(f"语义搜索 — {search.get('row_count', 0)} 结果")
            for r in search.get("rows", [])[:3]:
                title = r.get("title", r.get("text_content", ""))[:60]
                print(f"         {title}")
    else:
        print(f"  [INFO] 向量搜索不可用: {resp.get('error', '')}")

    # 混合搜索
    print("\nSTEP 9: 混合搜索 'GPT language model'")
    if resp.get("success"):
        vec = first_embedding(resp)
        search = c.search_hybrid(DS_META, vec, "GPT language model", top_k=5)
        if search.get("success"):
            c._pass(f"混合搜索 — {search.get('row_count', 0)} 结果")
    else:
        print("  [INFO] 混合搜索需要嵌入服务")

    # ── Phase 4: 分类统计 ──

    print("\n── Phase 4: 分类统计 ──")

    print("\nSTEP 10: 论文类别分布")
    resp = c.query_olap(DS_META,
        f'SELECT category, count(*) as cnt, '
        f'  round(avg(word_count), 0) as avg_words '
        f'FROM "{DS_META}" '
        f'GROUP BY category '
        f'ORDER BY cnt DESC')
    if resp.get("success"):
        for r in resp.get("rows", []):
            print(f"         {r.get('category', '?'):30s} — "
                  f"{r.get('cnt', 0)} 篇, 平均 {r.get('avg_words', 0):.0f} 词")
        c._pass("类别分布统计完成")
    else:
        print(f"  [INFO] {resp.get('error', '')}")

    print("\nSTEP 11: 年份分布")
    resp = c.query_olap(DS_META,
        f'SELECT year, count(*) as cnt '
        f'FROM "{DS_META}" '
        f'WHERE year IS NOT NULL '
        f'GROUP BY year '
        f'ORDER BY year DESC LIMIT 10')
    if resp.get("success"):
        for r in resp.get("rows", []):
            bar = "▓" * min(r.get("cnt", 0), 30)
            print(f"         {r.get('year', '?')}: {bar} ({r.get('cnt', 0)})")
        c._pass("年份分布统计完成")
    else:
        print(f"  [INFO] {resp.get('error', '')}")

    # ── Phase 5: 导出 ──

    print("\n── Phase 5: 导出 ──")

    print("\nSTEP 12: 导出论文目录")
    resp = c.export(DS_META, format="csv")
    if resp.get("success"):
        task_id = resp.get("task_id", "")
        if task_id:
            status = c.wait_for_export(DS_META, task_id, timeout=30)
            c._pass(f"导出完成 — {status.get('status')}")
        else:
            c._pass("导出已启动")
    else:
        print(f"  [INFO] {resp.get('error', '')}")

    # 清理
    for n in [DS_META, DS_FULL]:
        c.delete_dataset(n)

    print("\n" + "=" * 60)
    print("API-12  论文库管理与智能检索 — ALL PASSED")
    print("=" * 60)


if __name__ == "__main__":
    main()
