#!/usr/bin/env python3
"""20 — RAG 问答系统

场景: 基于知识库构建 RAG 问答，验证检索增强效果。

数据文件: datas/kb/knowledge_zh.jsonl

前提: 本地 LLM 服务 (Ollama/OpenAI) 可用 (config 中 rag.enabled=true)
"""

from __future__ import annotations

import argparse

import asyncio
import shutil
import sys
from pathlib import Path

import pyarrow as pa

from arrow_lake import Lake
from arrow_lake.rag.prompt import PromptRegistry

DATAS_DIR = Path(__file__).resolve().parent.parent / "datas"
_DEFAULT_BASE_URI = "./_tmp_rag_qa"
DIM = 768
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
    lake.append_dataset(dataset, vec_table)
    return n_rows


async def run_async() -> None:
    parser = argparse.ArgumentParser(description="20_rag_qa_system.py")
    parser.add_argument("--base-uri", default=_DEFAULT_BASE_URI)
    parser.add_argument("--no-cleanup", action="store_true")
    args = parser.parse_args()
    no_cleanup = args.no_cleanup
    print("=" * 60)
    print("20 RAG 问答系统")
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

    # STEP 1: 摄入知识库
    print("STEP 1: 摄入中文知识库")
    report = lake.ingest("knowledge_zh", [str(DATAS_DIR / "kb" / "knowledge_zh.jsonl")])
    print(f"  摄入: {report.total_rows} 行")

    # STEP 2: 建索引
    print("\nSTEP 2: 生成向量 + 建立索引")
    n = _add_vectors(lake, "knowledge_zh", report.total_rows)
    try:
        lake.create_vector_index("knowledge_zh", "text_embedding")
    except Exception as e:
        print(f"  向量索引跳过: {e}")
    lake.create_fts_index("knowledge_zh", fts_column="text_content")
    print(f"  {n} 个向量, 双索引已建立")

    # STEP 3: 检查 RAG 服务
    print("\nSTEP 3: 检查 RAG 服务")
    rag_ready = True
    try:
        rag_cfg = lake._config.rag
        if rag_cfg and getattr(rag_cfg, "enabled", False):
            print("  RAG 服务已启用")
        else:
            rag_ready = False
            print("  RAG 未启用 (config 中 rag.enabled 未设置)")
    except Exception as e:
        rag_ready = False
        print(f"  RAG 配置检查: {e}")

    # STEP 4: 列出可用模板 (不需要 LLM)
    print("\nSTEP 4: RAG 提示模板")
    try:
        registry = PromptRegistry()
        templates = registry.list_templates()
        print(f"  可用模板: {len(templates)} 个")
        for t in templates:
            print(f"    - {t}")
    except Exception as e:
        print(f"  模板列表: {e}")

    # STEP 5: RAG 单轮问答
    if rag_ready:
        print("\nSTEP 5: RAG 单轮问答")
        try:
            response = await lake.rag_query(
                "什么是向量数据库？它有哪些优势？",
                "knowledge_zh",
                top_k=3,
            )
            print(f"  回答: {response.answer[:200]}...")
            print(f"  引用: {len(response.citations)} 条")
            for cite in response.citations[:3]:
                print(f"    - {cite}")
        except Exception as e:
            print(f"  问答失败: {e}")

        # STEP 6: RAG 多轮对话
        print("\nSTEP 6: RAG 多轮对话 (session)")
        session_id = "demo_session_001"
        questions = [
            "Arrow 格式是什么？",
            "它和 Parquet 有什么关系？",
        ]
        for i, q in enumerate(questions):
            try:
                response = await lake.rag_query(
                    q,
                    "knowledge_zh",
                    top_k=3,
                    session_id=session_id,
                )
                print(f"  Q{i+1}: {q}")
                print(f"  A{i+1}: {response.answer[:150]}...")
            except Exception as e:
                print(f"  Q{i+1} 失败: {e}")

        # STEP 7: 清理过期会话
        print("\nSTEP 7: 清理过期会话")
        try:
            cleaned = lake.rag_cleanup_expired_sessions()
            print(f"  已清理: {cleaned} 个过期会话")
        except Exception as e:
            print(f"  清理: {e}")
    else:
        print("\n  [RAG 服务不可用，跳过问答步骤]")
        print("\n  启动指引:")
        print("    1. 启动 LLM 服务: ollama serve (或 OpenAI API)")
        print("    2. 在 config 中设置 rag.enabled=true")
        print("    3. 配置 rag.llm_provider='ollama' 或 'openai'")
        print("    4. 重新运行本示例")

    print("\n  [全部 PASS]" if rag_ready else "\n  [部分 PASS — RAG 不可用]")
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
