"""Verify v1.7.0 §13.3.1 ZhipuAI GLM-4-Plus integration + he extraction.

Reads the API key from ``ZHIPU_API_KEY`` env (never hardcode). Tests:

1. ``create_llm_provider(provider=zhipu, model=glm-4-plus)`` connectivity
2. ``HyperExtractExtractor`` + GLM-4-Plus end-to-end extraction on a sample,
   comparable to verify_he_model_compat.py (qwen3 baseline).

Usage:
    ZHIPU_API_KEY=<key> .venv/bin/python3 scripts/verify_zhipu_he.py
"""

from __future__ import annotations

import asyncio
import os
import sys

from arrow_lake.config import LLMConfig, LLMProviderType
from arrow_lake.knowledge_graph.doc_type_router import DocTypeRouter
from arrow_lake.knowledge_graph.he_extractor import HyperExtractExtractor
from arrow_lake.rag.provider import LLMMessage, create_llm_provider

ZHIPU_BASE = "https://open.bigmodel.cn/api/paas/v4"
MODEL = "glm-4-plus"
TEMPLATE = "general/biography_graph"

SAMPLE = (
    "OpenAI 发布了 GPT-4，该模型基于 Transformer 架构，"
    "由 Google 团队最初提出。"
)


async def main() -> None:
    key = os.environ.get("ZHIPU_API_KEY")
    if not key:
        print("FAIL: set ZHIPU_API_KEY env var")
        sys.exit(1)

    cfg = LLMConfig(
        provider=LLMProviderType.ZHIPU,
        model=MODEL,
        api_key=key,
        api_base=ZHIPU_BASE,
        temperature=0.0,
        max_tokens=2048,
        timeout_seconds=60.0,
    )

    # 1. Provider connectivity (project httpx provider, not langchain)
    provider = create_llm_provider(cfg)
    try:
        resp = await provider.generate([LLMMessage(role="user", content="Reply with just OK")])
        print(f"[connect] {MODEL}: {resp.content[:80]!r}")
    finally:
        await provider.close()

    # 2. he_extractor (langchain ChatOpenAI under the hood) end-to-end
    he = HyperExtractExtractor(
        cfg,
        doc_type_router=DocTypeRouter({}, default_template=TEMPLATE),
        language="zh",
    )
    result = await he.extract(SAMPLE, chunk_id="zhipu-verify")

    print(f"\n[he] template={TEMPLATE} model={MODEL}")
    print(f"[he] entities={len(result.entities)} relations={len(result.relations)}")
    for e in result.entities:
        print(f"  E {e.entity_type:14} {e.name}")
    for r in result.relations:
        print(f"  R {r.source} --{r.relation_type}--> {r.target}")

    if not result.entities:
        print("\n⚠️  No entities extracted — check model structured-output support")
        sys.exit(2)
    print("\n✅ ZhipuAI GLM-4-Plus + he_extractor verified")


if __name__ == "__main__":
    asyncio.run(main())
