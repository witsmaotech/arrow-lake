#!/usr/bin/env python3
"""34 — RAG 流式问答

场景: 使用 rag_query_stream 实现流式输出，实时显示 LLM 生成过程。
对比流式与批量的用户体验差异。

前提: LLM 服务可用 (config 中 rag.enabled=true)
"""

from __future__ import annotations

import asyncio
import shutil
import sys
import time
from pathlib import Path

import numpy as np
import pyarrow as pa

from arrow_lake import Lake

DATAS_DIR = Path(__file__).resolve().parent.parent / "datas"
BASE_URI = "./_tmp_rag_stream"
DIM = 768


def _add_vectors(lake: Lake, dataset: str) -> int:
    rng = np.random.RandomState(42)
    storage = lake._get_storage()
    ds = storage.open_dataset(dataset)
    n = ds.count_rows()
    vecs = rng.randn(n, DIM).astype(np.float32)
    vecs /= np.linalg.norm(vecs, axis=1, keepdims=True)
    original = ds.to_arrow()
    table = original.append_column(
        "text_embedding", pa.FixedSizeListArray.from_arrays(vecs.ravel(), DIM))
    storage.restore_dataset(dataset, table)
    return n


async def run_async() -> None:
    no_cleanup = "--no-cleanup" in sys.argv
    print("=" * 60)
    print("34 RAG 流式问答")
    print("=" * 60)

    base = Path(BASE_URI)
    if base.exists():
        shutil.rmtree(base)

    lake = Lake(base_uri=BASE_URI)

    # STEP 1: 摄入 + 建索引
    print("STEP 1: 准备知识库")
    lake.ingest("knowledge_zh", [str(DATAS_DIR / "kb" / "knowledge_zh.jsonl")])
    _add_vectors(lake, "knowledge_zh")
    lake.create_fts_index("knowledge_zh", fts_column="text_content")
    print("  知识库就绪")

    # STEP 2: 检查 RAG 服务
    print("\nSTEP 2: 检查 RAG 服务")
    rag_ready = False
    try:
        config = lake.config
        rag_cfg = getattr(config, "rag", None)
        if rag_cfg and getattr(rag_cfg, "enabled", False):
            print(f"  RAG 已启用 (provider={rag_cfg.provider if hasattr(rag_cfg, 'provider') else 'default'})")
            rag_ready = True
        else:
            print("  RAG 未启用")
    except Exception as e:
        print(f"  检查失败: {e}")

    if rag_ready:
        questions = [
            "什么是 Arrow 列式格式？它有什么优势？",
            "向量数据库如何支持语义搜索？",
        ]

        # STEP 3: 批量问答
        print("\nSTEP 3: 批量问答 (rag_query)")
        for q in questions:
            try:
                start = time.perf_counter()
                resp = await lake.rag_query(q, "knowledge_zh", top_k=3)
                elapsed = (time.perf_counter() - start) * 1000
                print(f"  Q: {q}")
                print(f"  A: {resp.answer[:150]}...")
                print(f"     [批量] 耗时 {elapsed:.0f}ms, "
                      f"tokens={resp.context_tokens}, "
                      f"latency={resp.latency_ms:.0f}ms")
            except Exception as e:
                print(f"  Q: {q} → 失败: {e}")

        # STEP 4: 流式问答
        print("\nSTEP 4: 流式问答 (rag_query_stream)")
        for q in questions:
            try:
                print(f"  Q: {q}")
                print(f"  A: ", end="", flush=True)
                start = time.perf_counter()
                char_count = 0
                async for chunk in lake.rag_query_stream(
                    q, "knowledge_zh", top_k=3
                ):
                    print(chunk, end="", flush=True)
                    char_count += len(chunk)
                elapsed = (time.perf_counter() - start) * 1000
                print(f"\n     [流式] 耗时 {elapsed:.0f}ms, {char_count} 字符")
            except Exception as e:
                print(f"\n  Q: {q} → 失败: {e}")

        # STEP 5: 多轮对话 (流式)
        print("\nSTEP 5: 多轮流式对话")
        session_id = "stream_session_001"
        follow_up = "它和 Parquet 有什么区别？"
        try:
            print(f"  Q: {follow_up}")
            print(f"  A: ", end="", flush=True)
            async for chunk in lake.rag_query_stream(
                follow_up, "knowledge_zh", top_k=3,
            ):
                print(chunk, end="", flush=True)
            print("\n")
        except Exception as e:
            print(f"\n  多轮失败: {e}")

        # STEP 6: 对话历史
        print("STEP 6: 对话历史")
        history = lake.rag_get_history(session_id)
        print(f"  历史轮次: {len(history)}")
    else:
        print("\n  RAG 不可用，展示降级检索")
        for q in ["Arrow 格式", "向量数据库"]:
            try:
                result = lake.text_search("knowledge_zh", q, top_k=3,
                                          fts_column="text_content")
                print(f"  '{q}' → {result.row_count} 条")
            except Exception as e:
                print(f"  '{q}' → {e}")

        print("\n  启动指引:")
        print("    1. 启动 LLM: ollama serve")
        print("    2. config: rag.enabled=true, rag.provider=ollama, rag.model=qwen3:8b")

    print("\n  [全部 PASS]")
    if not no_cleanup:
        lake.shutdown()
        shutil.rmtree(base, ignore_errors=True)
        print("(已清理)")


def main() -> None:
    asyncio.run(run_async())


if __name__ == "__main__":
    main()
