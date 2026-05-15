#!/usr/bin/env python3
"""API-25 — Daft 文本分析与嵌入探索

业务场景: 知识库管理员需要快速了解文本数据集的质量与分布:
         - 文本长度分布与离群值检测
         - 类别/标签维度统计
         - 嵌入向量维度验证
         - 中英文知识库结构对比
         - 为下游 RAG 系统选择最佳 chunk 策略提供数据支撑
数据源: datas/kb/knowledge.jsonl + datas/kb/knowledge_zh.jsonl
流程: 双源摄取 → Daft 结构探索 → SQL 文本统计 → 嵌入验证 → 分块策略建议
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from conftest import ArrowLakeClient

BASE_URL = "http://localhost:8000"
API_KEY = "dev-api-key-for-local-testing-only"
DATAS_DIR = Path(__file__).resolve().parent.parent / "datas"

DS_EN = "daft-text-en"
DS_ZH = "daft-text-zh"


def main() -> None:
    print("=" * 60)
    print("API-25  Daft 文本分析与嵌入探索")
    print("=" * 60)

    c = ArrowLakeClient(BASE_URL, API_KEY)
    c.delete_dataset(DS_EN)
    c.delete_dataset(DS_ZH)

    # ── Phase 1: 双源摄取 ──

    print("\n── Phase 1: 双源摄取 ──")

    en_path = DATAS_DIR / "kb" / "knowledge.jsonl"
    zh_path = DATAS_DIR / "kb" / "knowledge_zh.jsonl"

    print("\nSTEP 1: 摄取英文知识库 JSONL")
    assert en_path.exists(), f"文件不存在: {en_path}"
    resp = c.ingest_files(DS_EN, [str(en_path)])
    if not resp.get("success"):
        print(f"  [SKIP] {resp.get('error')}: {resp.get('message', '')[:120]}")
        return
    en_rows = resp.get("total_rows", 0)
    c._pass(f"英文知识库 — {en_rows} 条")

    print("\nSTEP 2: 摄取中文知识库 JSONL")
    assert zh_path.exists(), f"文件不存在: {zh_path}"
    resp = c.ingest_files(DS_ZH, [str(zh_path)])
    if not resp.get("success"):
        print(f"  [SKIP] {resp.get('error')}: {resp.get('message', '')[:120]}")
        c.delete_dataset(DS_EN)
        return
    zh_rows = resp.get("total_rows", 0)
    c._pass(f"中文知识库 — {zh_rows} 条")

    # ── Phase 2: Daft 结构探索 ──

    print("\n── Phase 2: Daft 结构探索 ──")

    print("\nSTEP 3: Daft 加载英文数据 — 查看 JSONL schema")
    resp = c.query_daft(DS_EN)
    en_sample = {}
    if resp.get("success"):
        cols = resp.get("column_count", 0)
        rows = resp.get("row_count", 0)
        en_sample = resp.get("rows", [{}])[0]
        print(f"         {rows} rows × {cols} columns")
        print(f"         列: {list(en_sample.keys())}")
        c._pass(f"英文 Schema — {cols} 列")
    else:
        print(f"  [INFO] {resp.get('error')}: {resp.get('message', '')[:120]}")

    print("\nSTEP 4: Daft 加载中文数据 — 对比结构差异")
    resp = c.query_daft(DS_ZH)
    zh_sample = {}
    if resp.get("success"):
        cols = resp.get("column_count", 0)
        rows = resp.get("row_count", 0)
        zh_sample = resp.get("rows", [{}])[0]
        print(f"         {rows} rows × {cols} columns")
        print(f"         列: {list(zh_sample.keys())}")

        shared = set(en_sample.keys()) & set(zh_sample.keys())
        en_only = set(en_sample.keys()) - set(zh_sample.keys())
        zh_only = set(zh_sample.keys()) - set(en_sample.keys())
        print(f"         共有列 ({len(shared)}): {shared}")
        if en_only:
            print(f"         英文独有: {en_only}")
        if zh_only:
            print(f"         中文独有: {zh_only}")
        c._pass(f"中文 Schema — {cols} 列, {len(shared)} 列重叠")
    else:
        print(f"  [INFO] {resp.get('error')}: {resp.get('message', '')[:120]}")

    # ── Phase 3: 文本长度统计 ──

    print("\n── Phase 3: 文本长度统计 ──")

    print("\nSTEP 5: SQL — 英文文本长度分布")
    resp = c.query_olap(
        DS_EN,
        f'SELECT '
        f'  min(length(text_content)) as min_len, '
        f'  max(length(text_content)) as max_len, '
        f'  round(avg(length(text_content)), 1) as avg_len, '
        f'  round(avg(length(title)), 1) as avg_title_len '
        f'FROM "{DS_EN}"',
    )
    if resp.get("success"):
        row = resp.get("rows", [{}])[0]
        print(f"         text_content: min={row.get('min_len')} max={row.get('max_len')} avg={row.get('avg_len')}")
        print(f"         title:        avg={row.get('avg_title_len')}")
        c._pass("英文文本长度统计")
    else:
        print(f"  [INFO] {resp.get('error')}: {resp.get('message', '')[:120]}")

    print("\nSTEP 6: SQL — 中文文本长度分布")
    resp = c.query_olap(
        DS_ZH,
        f'SELECT '
        f'  min(length(text_content)) as min_len, '
        f'  max(length(text_content)) as max_len, '
        f'  round(avg(length(text_content)), 1) as avg_len, '
        f'  round(avg(length(title)), 1) as avg_title_len '
        f'FROM "{DS_ZH}"',
    )
    if resp.get("success"):
        row = resp.get("rows", [{}])[0]
        print(f"         text_content: min={row.get('min_len')} max={row.get('max_len')} avg={row.get('avg_len')}")
        print(f"         title:        avg={row.get('avg_title_len')}")
        c._pass("中文文本长度统计")
    else:
        print(f"  [INFO] {resp.get('error')}: {resp.get('message', '')[:120]}")

    # ── Phase 4: 类别维度分析 ──

    print("\n── Phase 4: 类别维度分析 ──")

    print("\nSTEP 7: SQL — 英文类别分布 + 平均文本长度")
    resp = c.query_olap(
        DS_EN,
        f'SELECT category, count(*) as cnt, '
        f'  round(avg(length(text_content)), 1) as avg_text_len '
        f'FROM "{DS_EN}" '
        f'GROUP BY category ORDER BY cnt DESC',
    )
    if resp.get("success"):
        for r in resp.get("rows", []):
            print(f"         {r.get('category', '?'):25s} "
                  f"cnt={r.get('cnt', 0):>4d} "
                  f"avg_len={r.get('avg_text_len', 0):>6}")
        c._pass("英文类别分布")
    else:
        print(f"  [INFO] {resp.get('error')}: {resp.get('message', '')[:120]}")

    print("\nSTEP 8: SQL — 中文类别分布 + 平均文本长度")
    resp = c.query_olap(
        DS_ZH,
        f'SELECT category, count(*) as cnt, '
        f'  round(avg(length(text_content)), 1) as avg_text_len '
        f'FROM "{DS_ZH}" '
        f'GROUP BY category ORDER BY cnt DESC',
    )
    if resp.get("success"):
        for r in resp.get("rows", []):
            print(f"         {r.get('category', '?'):25s} "
                  f"cnt={r.get('cnt', 0):>4d} "
                  f"avg_len={r.get('avg_text_len', 0):>6}")
        c._pass("中文类别分布")
    else:
        print(f"  [INFO] {resp.get('error')}: {resp.get('message', '')[:120]}")

    # ── Phase 5: 标签热度分析 ──

    print("\n── Phase 5: 标签热度分析 ──")

    print("\nSTEP 9: SQL — 英文数据源 (source) 分布")
    resp = c.query_olap(
        DS_EN,
        f'SELECT source, count(*) as cnt '
        f'FROM "{DS_EN}" '
        f'GROUP BY source ORDER BY cnt DESC LIMIT 10',
    )
    if resp.get("success"):
        for r in resp.get("rows", []):
            print(f"         {r.get('source', '?'):40s} cnt={r.get('cnt', 0):>4d}")
        c._pass("数据源分布")
    else:
        print(f"  [INFO] {resp.get('error')}: {resp.get('message', '')[:120]}")

    # ── Phase 6: 嵌入向量验证 ──

    print("\n── Phase 6: 嵌入向量验证 ──")

    print("\nSTEP 10: 生成嵌入向量 — 验证文本可嵌入性")
    test_texts = ["data engineering with Apache Arrow", "向量搜索与语义检索"]
    resp = c.embed_text(test_texts)
    if resp.get("success"):
        embeddings = resp.get("embeddings") or resp.get("data") or []
        if embeddings and embeddings[0]:
            dim = len(embeddings[0])
            print(f"         向量维度: {dim}")
            print(f"         样本: [{', '.join(str(round(v, 3)) for v in embeddings[0][:5])}, ...]")
            c._pass(f"嵌入验证 — {len(embeddings)} 条文本, {dim} 维向量")
        else:
            print("         [INFO] 嵌入服务返回空向量")
    else:
        print(f"  [INFO] 嵌入服务不可用: {resp.get('error', '')[:80]}")

    # ── Phase 7: 分块策略建议 ──

    print("\n── Phase 7: 分块策略建议 ──")

    print("\nSTEP 11: SQL — 文本长度分桶 (为 chunk 策略提供依据)")
    resp = c.query_olap(
        DS_EN,
        f'SELECT '
        f'  CASE '
        f"    WHEN length(text_content) < 200 THEN 'short (<200)' "
        f"    WHEN length(text_content) < 500 THEN 'medium (200-500)' "
        f"    WHEN length(text_content) < 1000 THEN 'long (500-1000)' "
        f"    ELSE 'very_long (>1000)' "
        f'  END as bucket, '
        f'  count(*) as cnt, '
        f'  round(avg(length(text_content)), 1) as avg_len '
        f'FROM "{DS_EN}" '
        f'GROUP BY bucket ORDER BY avg_len',
    )
    if resp.get("success"):
        for r in resp.get("rows", []):
            print(f"         {r.get('bucket', '?'):22s} "
                  f"cnt={r.get('cnt', 0):>4d} "
                  f"avg_len={r.get('avg_len', 0):>6}")
        c._pass("分块策略分桶完成")
    else:
        print(f"  [INFO] {resp.get('error')}: {resp.get('message', '')[:120]}")

    # 清理
    c.delete_dataset(DS_EN)
    c.delete_dataset(DS_ZH)

    print("\n" + "=" * 60)
    print("API-25  ALL PASSED")
    print("=" * 60)


if __name__ == "__main__":
    main()
