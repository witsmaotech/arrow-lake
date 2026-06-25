#!/usr/bin/env python3
"""34 — RAG 流式问答

场景: 使用 rag_query_stream 实现流式输出，实时显示 LLM 生成过程。
对比流式与批量的用户体验差异。

前提: LLM 服务可用 (config 中 rag.enabled=true)
"""

from __future__ import annotations

import argparse

import asyncio
import shutil
import sys
import time
from pathlib import Path

import pyarrow as pa

from arrow_lake import Lake

DATAS_DIR = Path(__file__).resolve().parent.parent / "datas"
_DEFAULT_BASE_URI = "./_tmp_rag_stream"
DIM = 768

# 本脚本创建的所有数据集
_DATASETS = ["knowledge_zh"]


def _add_vectors(lake: Lake, dataset: str, n_rows: int) -> int:
    """生成随机向量并追加到数据集 (模拟嵌入模型)"""
    import numpy as np
    rng = np.random.RandomState(42)
    vecs = rng.randn(n_rows, DIM).astype(np.float32)
    vecs /= np.linalg.norm(vecs, axis=1, keepdims=True)
    vec_table = pa.table({
        "text_embedding": pa.FixedSizeListArray.from_arrays(vecs.ravel(), DIM),
    })
    return n_rows


async def run_async() -> None:
    parser = argparse.ArgumentParser(description="34_rag_streaming.py")
    parser.add_argument("--base-uri", default=_DEFAULT_BASE_URI)
    parser.add_argument("--no-cleanup", action="store_true")
    args = parser.parse_args()
    no_cleanup = args.no_cleanup
    print("=" * 60)
    print("34 RAG 流式问答")
    print("=" * 60)

    base = Path(args.base_uri)
    if base.exists():
        shutil.rmtree(base)

    lake = Lake(base_uri=args.base_uri)

    # 清理后端残留
    for ds in _DATASETS:
        try:
            lake.delete_dataset(ds)
        except Exception:
            pass

    # STEP 1: 摄入 + 建索引
    print("STEP 1: 准备知识库")
    report = lake.ingest("knowledge_zh", [str(DATAS_DIR / "kb" / "knowledge_zh.jsonl")])
    _add_vectors(lake, "knowledge_zh", report.total_rows)
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
        except (ValueError, RuntimeError) as e:
            print(f"\n  多轮失败: {e}")

        # STEP 6: 清理过期会话
        print("STEP 6: 清理过期会话")
        cleaned = lake.rag_cleanup_expired_sessions()
        print(f"  已清理: {cleaned} 个过期会话")
    else:
        print("\n  RAG 不可用，展示降级检索")
        for q in ["Arrow 格式", "向量数据库"]:
            try:
                result = lake.text_search("knowledge_zh", q, top_k=3,
                                          columns=["text_content"])
                print(f"  '{q}' → {result.row_count} 条")
            except (ValueError, RuntimeError) as e:
                print(f"  '{q}' → {e}")

        print("\n  启动指引:")
        print("    1. 启动 LLM: ollama serve")
        print("    2. config: rag.enabled=true, rag.provider=ollama, rag.model=qwen3:8b")

    print("\n  [全部 PASS]")
    if not no_cleanup:
        for ds in _DATASETS:
            try:
                lake.delete_dataset(ds)
            except Exception:
                pass
        lake.shutdown()
        shutil.rmtree(base, ignore_errors=True)
        print("(已清理)")


def main() -> None:
    asyncio.run(run_async())


if __name__ == "__main__":
    main()
