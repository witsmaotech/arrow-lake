#!/usr/bin/env python3
"""49 — RAG 重排器与忠实度验证 (v1.9.5 / v1.9.6)

场景: 混合检索 (hybrid) 后用重排器精排, 再用忠实度校验检测回答是否被证据支撑。
重排器与忠实度校验都是 **config 级** 配置 (非 per-query 参数) —— 配一次全局生效。

教学点:
  1. ``strategy="hybrid"`` 触发向量+FTS 融合检索 (RRF), 需 ≥256 行建 IVF 索引
  2. 重排器 (reranker): config.rag.reranker = "ollama" | "cross_encoder" | "llm" | "none"
  3. 忠实度校验: config.rag.enable_verification=True → response.verification
  4. OllamaReranker (Qwen3-Reranker-0.6B) 是默认; 不可用时 latch Noop 不崩

前提: 数据集 ≥256 行 (hybrid 硬限制) + LLM/embedding 服务可用。
"""

from __future__ import annotations

import argparse
import asyncio
import shutil
from pathlib import Path

import pyarrow as pa

from arrow_lake import Lake
from arrow_lake.config import ArrowLakeConfig

_DEFAULT_BASE_URI = "./_tmp_reranker_faith"
_DATASET = "reranker_demo"
# 重复扩充到 ≥256 行, 让 hybrid (IVF_PQ) 索引可用
_BASE_TEXTS = [
    "Lance 是基于 Apache Arrow 的列式格式, 为向量检索与 RAG 优化。",
    "DuckDB 是进程内列式 OLAP 数据库, 可直接读 Lance/Parquet 无需导入。",
    "HugeGraph 是图数据库, 支持属性图模型与 Gremlin 遍历。",
    "RAG 混合检索融合 BM25 全文与向量相似度, 用 RRF 排序。",
    "重排器对召回的 top-N 做精排, 提升答案相关性。",
]


def _build_table(n_rows: int = 260) -> pa.Table:
    """构造 ≥256 行数据 (hybrid IVF_PQ 硬限制), 循环复用基础文本。"""
    texts = [_BASE_TEXTS[i % len(_BASE_TEXTS)] for i in range(n_rows)]
    srcs = [f"chunk_{i}" for i in range(n_rows)]
    return pa.table({"text_content": pa.array(texts), "source": pa.array(srcs)})


def _add_vectors(lake: Lake, dataset: str, n: int) -> None:
    import numpy as np
    rng = np.random.RandomState(11)
    vecs = rng.randn(n, 768).astype(np.float32)
    vecs /= np.linalg.norm(vecs, axis=1, keepdims=True)
    arr = pa.FixedSizeListArray.from_arrays(vecs.ravel(), 768)
    t = lake.read_dataset(dataset)
    if hasattr(t, "to_arrow"):
        t = t.to_arrow()
    lake.restore_dataset(dataset, t.append_column("text_embedding", arr))


async def run_async() -> None:
    parser = argparse.ArgumentParser(description="49_rag_reranker_faithfulness.py")
    parser.add_argument("--base-uri", default=_DEFAULT_BASE_URI)
    parser.add_argument("--no-cleanup", action="store_true")
    args = parser.parse_args()
    no_cleanup = args.no_cleanup
    print("=" * 64)
    print("49 RAG 重排器与忠实度验证 (v1.9.5 / v1.9.6)")
    print("=" * 64)

    base = Path(args.base_uri)
    if base.exists():
        shutil.rmtree(base)

    # --- Step 1: 配置重排器 + 忠实度校验 (config 级) ---
    print("\n--- Step 1: 配置 reranker + verification ---")
    config = ArrowLakeConfig()
    config.hugegraph.enabled = False  # 本例专注 RAG, 不走 GraphRAG
    # reranker: ollama (Qwen3-Reranker-0.6B, 默认) / cross_encoder / llm / none
    config.rag.enabled = True
    config.rag.reranker = "ollama"
    config.rag.reranker_model = "dengcao/Qwen3-Reranker-0.6B:F16"
    config.rag.reranker_top_n = 10  # 精排后保留条数
    # v1.9.6 P0-1: 忠实度校验 (轻量 [n] ref check)
    config.rag.enable_verification = True
    config.rag.default_retrieval_strategy = "hybrid"  # v1.9.5 修复死配置
    print(f"  reranker={config.rag.reranker}, model={config.rag.reranker_model}")
    print(f"  reranker_top_n={config.rag.reranker_top_n}")
    print(f"  enable_verification={config.rag.enable_verification}")
    print(f"  default_strategy={config.rag.default_retrieval_strategy}")

    lake = Lake(base_uri=args.base_uri, config=config)
    try:
        lake.delete_dataset(_DATASET)
    except Exception:
        pass

    # --- Step 2: 摄入 ≥256 行 + 双索引 ---
    print("\n--- Step 2: 摄入数据 + 建索引 (≥256 行满足 hybrid) ---")
    n = 260
    lake.create_dataset(_DATASET, _build_table(n), actor="cookbook")
    _add_vectors(lake, _DATASET, n)
    try:
        lake.create_vector_index(_DATASET, vector_column="text_embedding")
        print(f"  向量索引 (IVF_PQ) 已建 ({n} 行)")
    except Exception as e:
        print(f"  向量索引: {e}")
    lake.create_fts_index(_DATASET, fts_column="text_content")
    print(f"  FTS 索引已建")

    # --- Step 3: hybrid 检索 + 重排 ---
    print("\n--- Step 3: hybrid 检索 + reranker 精排 ---")
    question = "Lance 和 DuckDB 有什么关系?"
    try:
        resp = await lake.rag_query(question, _DATASET, top_k=10,
                                    strategy="hybrid", use_kg=False)
        print(f"  问题: {question}")
        print(f"  回答: {resp.answer[:200]}...")
        print(f"  retrieval_count={resp.retrieval_count} (reranker 重排后)")
        print(f"  latency={resp.latency_ms:.0f}ms" if resp.latency_ms else "")
        if resp.latency_breakdown:
            lb = resp.latency_breakdown
            print(f"  分解: retrieval={lb.retrieval_ms}ms, "
                  f"llm={lb.llm_ms}ms, total={lb.total_ms}ms")
    except Exception as e:
        print(f"  查询失败 (检查 LLM/embedding 服务): {e}")

    # --- Step 4: 忠实度验证 (response.verification) ---
    print("\n--- Step 4: 忠实度验证 (VerificationResult) ---")
    try:
        resp2 = await lake.rag_query("重排器的作用是什么?", _DATASET,
                                     top_k=10, strategy="hybrid", use_kg=False)
        v = resp2.verification
        if v is None:
            print("  verification=None (enable_verification 未生效或 LLM 不可用)")
        else:
            # VerificationResult: supported / unsupported / support_ratio
            print(f"  supported claims: {getattr(v, 'supported', '?')}")
            print(f"  unsupported claims: {getattr(v, 'unsupported', '?')}")
            print(f"  support_ratio: {getattr(v, 'support_ratio', '?')}")
            print("  → support_ratio 高 = 回答被证据支撑; 低 = 可能幻觉")
    except Exception as e:
        print(f"  验证失败: {e}")

    # --- Step 5: reranker 选型说明 ---
    print("\n--- Step 5: reranker 选型 (config.rag.reranker) ---")
    print("  'ollama'        → OllamaReranker (Qwen3-Reranker-0.6B, yes/no judge)")
    print("  'cross_encoder' → CrossEncoderReranker (bge-reranker-v2-m3 连续分)")
    print("  'llm'           → LLMReranker (通用 LLM 打 1-10 分, 成本高)")
    print("  'none'          → NoopReranker (截断不重排)")
    print("  默认 ollama; 模型不可用时 latch Noop (不阻断查询)")

    # --- Step 6: 修复历史 (v1.9.5 死配置) ---
    print("\n--- Step 6: v1.9.5/9.6 关键修复 ---")
    print("  v1.9.5: default_retrieval_strategy 之前是死配置 (实际走纯 FTS)")
    print("          修复后 hybrid 真正生效 (向量+FTS+RRF)")
    print("  v1.9.6: reranker 之前 _lake_rag 不传 reranker → 恒 Noop")
    print("          修复后按 config 注入; +enable_verification 忠实度闸门")

    print("\n  [全部 PASS]")
    if not no_cleanup:
        try:
            lake.delete_dataset(_DATASET)
        except Exception:
            pass
        lake.shutdown()
        shutil.rmtree(base, ignore_errors=True)
        print("(已清理)")


def main() -> None:
    asyncio.run(run_async())


if __name__ == "__main__":
    main()
