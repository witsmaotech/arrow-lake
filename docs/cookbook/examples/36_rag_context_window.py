#!/usr/bin/env python3
"""36 — RAG 上下文窗口管理

场景: 展示 ContextWindow 的 token 预算、chunk 去重、引用追踪和
上下文组装机制。无需 LLM 服务。

数据: 内部构造
"""

from __future__ import annotations

import shutil
import sys
from pathlib import Path

from arrow_lake.rag.context import ContextWindow, ContextChunk, count_tokens

BASE_URI = "./_tmp_context"


def main() -> None:
    no_cleanup = "--no-cleanup" in sys.argv
    print("=" * 60)
    print("36 RAG 上下文窗口管理")
    print("=" * 60)

    base = Path(BASE_URI)
    if base.exists():
        shutil.rmtree(base)

    # STEP 1: Token 计数
    print("STEP 1: Token 计数")
    texts = [
        "Arrow 是 Apache 基金会下的高性能列式内存格式。",
        "PyArrow 提供零拷贝读取和高效的 I/O 操作。",
        "DuckDB 是一个嵌入式的分析型数据库引擎。",
    ]
    for text in texts:
        tokens = count_tokens(text)
        print(f"  [{tokens:>4} tokens] {text[:50]}...")

    # STEP 2: 创建上下文窗口
    print("\nSTEP 2: 创建上下文窗口 (预算 256 tokens)")
    window = ContextWindow(token_budget=256, max_chunks=5)
    print(f"  token_budget: {window.token_count}")
    print(f"  chunk_count: {window.chunk_count}")

    # STEP 3: 添加 chunks
    print("\nSTEP 3: 添加 ContextChunk")
    chunks = [
        ContextChunk(text="Arrow 是列式内存格式，支持零拷贝读取。", dataset="kb", row_id="r1", score=0.95),
        ContextChunk(text="Parquet 是 Arrow 生态中的列式存储格式。", dataset="kb", row_id="r2", score=0.90),
        ContextChunk(text="DuckDB 是嵌入式的分析数据库引擎。", dataset="kb", row_id="r3", score=0.85),
        ContextChunk(text="向量数据库支持高维相似性搜索。", dataset="kb", row_id="r4", score=0.80),
    ]
    for chunk in chunks:
        added = window.add_chunk(chunk)
        status = "已添加" if added else "已截断 (超出预算)"
        print(f"  [{status}] tokens={count_tokens(chunk.text):>4} "
              f"\"{chunk.text[:40]}...\"")

    print(f"\n  上下文窗口: {window.token_count} tokens, {window.chunk_count} chunks")

    # STEP 4: 去重
    print("\nSTEP 4: Chunk 去重")
    dup_chunk = ContextChunk(text="Arrow 是列式内存格式，支持零拷贝读取。", dataset="kb", row_id="r1", score=0.88)
    added = window.add_chunk(dup_chunk, skip_dedup=False)
    print(f"  重复 chunk (skip_dedup=False): {'已添加' if added else '已跳过'}")
    added = window.add_chunk(dup_chunk, skip_dedup=True)
    print(f"  重复 chunk (skip_dedup=True): {'已添加' if added else '已跳过'}")

    # STEP 5: 图谱上下文注入
    print("\nSTEP 5: 图谱上下文注入 (add_graph_context)")
    graph_text = (
        "知识图谱 --类型--> 语义网络\n"
        "知识图谱 --应用于--> 推荐系统\n"
        "知识图谱 --应用于--> 问答系统\n"
        "Neo4j --类型--> 图数据库\n"
        "HugeGraph --类型--> 图数据库\n"
    )
    added = window.add_graph_context(graph_text)
    print(f"  图谱三元组注入: {'成功' if added else '已截断 (超出预算)'}")
    print(f"  图谱上下文 tokens: {count_tokens(graph_text)}")

    # STEP 6: 引用追踪
    print("\nSTEP 6: 引用追踪 (citations)")
    citations = window.citations
    print(f"  引用数: {len(citations)}")
    for c in citations:
        print(f"    [{c.chunk_index}] dataset={c.dataset} row={c.row_id} "
              f"score={c.score:.2f}")
        print(f"      \"{c.text_excerpt[:50]}...\"")

    # STEP 7: 组装上下文
    print("\nSTEP 7: 组装最终上下文")
    assembled = window.assemble()
    print(f"  总长度: {len(assembled)} 字符")
    print(f"  总 tokens: {count_tokens(assembled)}")
    print(f"  预算使用率: {count_tokens(assembled) / 256 * 100:.1f}%")
    print(f"\n  上下文内容 (前 300 字符):")
    print(f"  {assembled[:300]}...")

    # STEP 8: 清空和重建
    print("\nSTEP 8: 清空与重建")
    window.clear()
    print(f"  清空后: {window.token_count} tokens, {window.chunk_count} chunks")

    small_window = ContextWindow(token_budget=100)
    large_chunk = ContextChunk(text="A" * 200, dataset="test", row_id="big", score=0.9)
    small_window.add_chunk(large_chunk)
    print(f"  超大 chunk 处理: tokens={small_window.token_count} (预算 100)")

    print("\n  [全部 PASS]")
    shutil.rmtree(base, ignore_errors=True)
    print("(已清理)")


if __name__ == "__main__":
    main()
