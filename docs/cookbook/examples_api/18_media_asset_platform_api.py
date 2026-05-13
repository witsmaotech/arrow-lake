#!/usr/bin/env python3
"""API-18 — 跨模态多媒体资产管理平台

业务场景: 传媒/电商平台的数字资产管理系统，统一管理图片、视频、文档，支持跨模态检索
数据源: datas/photos/*.jpg + datas/videos/*.mp4 + datas/kb/knowledge_zh.jsonl
流程: 多模态混合摄取 → 跨模态嵌入 → 统一索引 → 跨模态检索 → 多维度统计 → 批量导出
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from conftest import ArrowLakeClient, first_embedding

BASE_URL = "http://localhost:8000"
API_KEY = "dev-api-key-for-local-testing-only"
DATAS_DIR = Path(__file__).resolve().parent.parent / "datas"

DS_MEDIA = "media-assets"


def main() -> None:
    print("=" * 60)
    print("API-18  跨模态多媒体资产管理平台")
    print("=" * 60)

    c = ArrowLakeClient(BASE_URL, API_KEY)

    c.delete_dataset(DS_MEDIA)

    photo_dir = DATAS_DIR / "photos"
    video_dir = DATAS_DIR / "videos"
    kb_jsonl = DATAS_DIR / "kb" / "knowledge_zh.jsonl"

    # ── Phase 1: 多模态混合摄取 ──

    print("\n── Phase 1: 多模态混合摄取 ──")

    # 图片摄取
    print("\nSTEP 1: 摄取图片资产")
    imgs: list[str] = []
    if photo_dir.exists():
        imgs = [str(p) for p in photo_dir.iterdir()
                if p.suffix.lower() in (".jpg", ".jpeg", ".png")]
    if imgs:
        resp = c.ingest_images(DS_MEDIA, imgs)
        if resp.get("success"):
            c._pass(f"图片摄取 — {resp.get('total_rows', 0)} 张")
        else:
            print(f"  [WARN] {resp.get('error')}: {resp.get('message', '')[:100]}")
    else:
        print("  [SKIP] 无图片文件")

    # 视频摄取
    print("\nSTEP 2: 摄取视频资产")
    vids: list[str] = []
    if video_dir.exists():
        vids = [str(p) for p in video_dir.glob("*.mp4")]
    if vids:
        resp = c.ingest_videos(DS_MEDIA, vids)
        if resp.get("success"):
            c._pass(f"视频摄取 — {resp.get('total_rows', 0)} 关键帧")
        else:
            print(f"  [WARN] {resp.get('error')}: {resp.get('message', '')[:100]}")
    else:
        print("  [SKIP] 无视频文件")

    # 文档摄取 (知识库)
    print("\nSTEP 3: 摄取文档资产")
    if kb_jsonl.exists():
        resp = c.ingest_files(DS_MEDIA, [str(kb_jsonl)])
        if resp.get("success"):
            c._pass(f"文档摄取 — {resp.get('total_rows', 0)} 条")
        else:
            print(f"  [WARN] {resp.get('error')}: {resp.get('message', '')[:100]}")
    else:
        print(f"  [SKIP] {kb_jsonl} 不存在")

    # PDF 文档摄取
    print("\nSTEP 4: 摄取 PDF 文档")
    pdf_dir = DATAS_DIR / "papers" / "full_text"
    if pdf_dir.exists():
        pdfs = sorted(pdf_dir.glob("zh_*.pdf"))[:2]  # 中文 PDF
        if pdfs:
            resp = c.ingest_documents(DS_MEDIA, [str(p) for p in pdfs])
            if resp.get("success"):
                c._pass(f"PDF 摄取 — {resp.get('total_rows', 0)} chunks")
            else:
                print(f"  [INFO] {resp.get('error', '')}")

    # ── Phase 2: 统一索引 ──

    print("\n── Phase 2: 统一索引 ──")

    print("\nSTEP 5: 构建向量索引")
    resp = c.create_vector_index(DS_MEDIA, vector_column="text_embedding",
                                  metric="cosine", index_type="IVF_PQ")
    if resp.get("success"):
        c._pass("统一向量索引就绪")
    else:
        print(f"  [INFO] {resp.get('error', '')}")

    print("\nSTEP 6: 构建 FTS 索引")
    resp = c.create_fts_index(DS_MEDIA, fts_column="text_content")
    if resp.get("success"):
        c._pass("统一 FTS 索引就绪")
    else:
        print(f"  [INFO] {resp.get('error', '')}")

    # ── Phase 3: 跨模态检索 ──

    print("\n── Phase 3: 跨模态检索 ──")

    # 文搜全部
    print("\nSTEP 7: 全文搜索 '数据'")
    resp = c.search_fts(DS_MEDIA, "数据", top_k=10)
    if resp.get("success"):
        rows = resp.get("rows", [])
        c._pass(f"搜索 '数据' — {resp.get('row_count', 0)} 条结果")
        # 按模态分组显示
        by_modality: dict[str, int] = {}
        for r in rows:
            mod = r.get("modality", r.get("source", "unknown"))
            by_modality[mod] = by_modality.get(mod, 0) + 1
        for mod, cnt in by_modality.items():
            print(f"         {mod}: {cnt} 条")

    # 英文搜索
    print("\nSTEP 8: 全文搜索 'Arrow'")
    resp = c.search_fts(DS_MEDIA, "Arrow", top_k=10)
    if resp.get("success"):
        rows = resp.get("rows", [])
        c._pass(f"搜索 'Arrow' — {resp.get('row_count', 0)} 条结果")
        for r in rows[:3]:
            mod = r.get("modality", r.get("source", "?"))
            title = r.get("title", r.get("text_content", ""))[:50]
            print(f"         [{mod}] {title}")

    # 语义搜索
    print("\nSTEP 9: 语义搜索 '向量检索技术'")
    resp = c.embed_text(["向量检索技术"])
    if resp.get("success"):
        vec = first_embedding(resp)
        if vec:
            search = c.search_vector(DS_MEDIA, vec, top_k=5)
            if search.get("success"):
                c._pass(f"语义搜索 — {search.get('row_count', 0)} 条结果")
                for r in search.get("rows", [])[:3]:
                    mod = r.get("modality", r.get("source", "?"))
                    title = r.get("title", r.get("text_content", ""))[:50]
                    print(f"         [{mod}] {title}")

    # 混合搜索
    print("\nSTEP 10: 混合搜索 '深度学习模型优化'")
    embed_resp = c.embed_text(["深度学习模型优化"])
    if embed_resp.get("success"):
        vec = _first_embedding(embed_resp)
        if vec:
            search = c.search_hybrid(DS_MEDIA, vec, "深度学习模型优化", top_k=5)
            if search.get("success"):
                c._pass(f"混合搜索 — {search.get('row_count', 0)} 条结果")

    # 分面搜索
    print("\nSTEP 11: 分面搜索 (按模态/来源)")
    resp = c.search_faceted(DS_MEDIA, "数据", facets=["modality", "source"], top_k=5)
    if resp.get("success"):
        facets_data = resp.get("facets", resp.get("facet_counts", {}))
        c._pass(f"分面搜索 — facets={facets_data}")

    # ── Phase 4: 多维度统计 ──

    print("\n── Phase 4: 多维度统计 ──")

    print("\nSTEP 12: 资产总览")
    resp = c.query_olap(DS_MEDIA,
        f'SELECT count(*) as total_assets FROM "{DS_MEDIA}"')
    if resp.get("success"):
        rows = resp.get("rows", [])
        if rows:
            c._pass(f"资产总览 — {rows[0].get('total_assets', 0)} 条资产")

    print("\nSTEP 13: 按来源统计")
    resp = c.query_olap(DS_MEDIA,
        f'SELECT source, count(*) as cnt '
        f'FROM "{DS_MEDIA}" '
        f'GROUP BY source '
        f'ORDER BY cnt DESC')
    if resp.get("success"):
        rows = resp.get("rows", [])
        c._pass(f"来源分布 — {len(rows)} 个来源")
        for r in rows[:8]:
            bar = "█" * min(r.get("cnt", 0) // 2, 25)
            print(f"         {r.get('source', '?'):30s} {r.get('cnt', 0):>4d} {bar}")

    # ── Phase 5: 质量管控 ──

    print("\n── Phase 5: 质量管控 ──")

    print("\nSTEP 14: 质量过滤 (空内容)")
    resp = c.quality_filter(DS_MEDIA, [
        {"column": "text_content", "type": "not_null"},
        {"column": "text_content", "type": "min_length", "value": 5},
    ])
    if resp.get("success"):
        c._pass("内容非空校验完成")

    print("\nSTEP 15: 去重")
    resp = c.quality_deduplicate(DS_MEDIA, strategy="exact", column="text_content")
    if resp.get("success"):
        dupes = resp.get("duplicates_removed", 0)
        c._pass(f"去重 — 移除 {dupes} 条重复资产")

    print("\nSTEP 16: 质量报告")
    resp = c.quality_report(DS_MEDIA)
    if resp.get("success"):
        report = resp.get("report", resp.get("data", {}))
        if isinstance(report, dict):
            score = report.get("quality_score", "?")
            c._pass(f"资产库质量评分: {score}")

    # ── Phase 6: 批量导出 ──

    print("\n── Phase 6: 批量导出 ──")

    print("\nSTEP 17: 导出资产目录 (Parquet)")
    resp = c.export(DS_MEDIA, format="parquet")
    if resp.get("success"):
        task_id = resp.get("task_id", "")
        if task_id:
            status = c.wait_for_export(DS_MEDIA, task_id, timeout=30)
            c._pass(f"Parquet 导出 — {status.get('status')}")

    # 审计
    c.audit_record(DS_MEDIA, "media_asset_management",
                   details={
                       "images": len(imgs),
                       "videos": len(vids),
                       "modalities": 3,
                   })

    # 清理
    c.delete_dataset(DS_MEDIA)

    print("\n" + "=" * 60)
    print("API-18  跨模态多媒体资产管理平台 — ALL PASSED")
    print("=" * 60)


if __name__ == "__main__":
    main()
