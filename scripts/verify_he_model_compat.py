#!/usr/bin/env python3
"""§12.2 模型兼容性实测（go/no-go 关卡）。

验证项目实际 LLM（qwen3:30b-a3b via Ollama）能否驱动 hyper-extract 抽取，
产出合法 AutoGraph。hyper-extract 硬要求 LLM 支持 json_schema / function calling，
而 Qwen3 是思考模型 + Ollama 对结构化输出支持有限 —— 本脚本确认是否兼容。

运行：.venv/bin/python3 scripts/verify_he_model_compat.py
退出码：0=PASS，非 0=FAIL（2=依赖缺失，3/4=流程异常，5=空图）。
"""
from __future__ import annotations

import os
import sys

from arrow_lake.config import ArrowLakeConfig

# §12.6① ChatOpenAI(api_key=...) does not propagate to the underlying openai
# client in langchain-openai 1.3.x → OPENAI_API_KEY env must be set (Ollama
# does not validate the value, so "dummy" is fine for the local qwen3 case).
os.environ.setdefault("OPENAI_API_KEY", "dummy")

try:
    from langchain_openai import ChatOpenAI
    from hyperextract import Template
except ImportError as e:
    print(f"FAIL: dependency missing: {e}")
    sys.exit(2)


SAMPLE = (
    "OpenAI 发布了 GPT-4，该模型基于 Transformer 架构，"
    "由 Google 团队最初提出。特斯拉创始人马斯克也投资了 AI 领域。"
)


def main() -> int:
    cfg = ArrowLakeConfig()
    lc = cfg.llm
    print(
        f"[config] provider={lc.provider} model={lc.model} "
        f"api_base={lc.api_base or '(default)'}"
    )

    chat = ChatOpenAI(
        model=lc.model,
        api_key=lc.api_key or "dummy",
        base_url=lc.api_base or None,
        temperature=0,
        max_tokens=2048,
    )

    print("[step] Template.create('general/biography_graph', 'zh', embedder=None)")
    try:
        ka = Template.create(
            "general/biography_graph", "zh", llm_client=chat, embedder=None
        )
    except Exception as e:
        print(f"FAIL Template.create: {type(e).__name__}: {e}")
        return 3

    print("[step] ka.parse(sample)")
    try:
        result = ka.parse(SAMPLE)
    except Exception as e:
        print(f"FAIL parse: {type(e).__name__}: {e}")
        return 4

    nodes = getattr(result, "nodes", None) or []
    edges = getattr(result, "edges", None) or []
    print(f"[result] nodes={len(nodes)} edges={len(edges)}")
    for n in nodes[:3]:
        print(f"  node: {n}")
    for e in edges[:3]:
        print(f"  edge: {e}")

    if len(nodes) > 0:
        print("PASS: 项目 LLM 可驱动 hyper-extract 产出合法 AutoGraph")
        return 0
    print("FAIL: 产出空图（疑似思考标签/结构化输出不兼容）")
    return 5


if __name__ == "__main__":
    sys.exit(main())
