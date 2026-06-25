"""P3 end-to-end: doc_type INFERRED from content (not passed by caller).

Scenario: a real PDF chunk is extracted with NO doc_type supplied. The
DocTypeClassifier (qwen3) infers the type from content, the router resolves the
template, and HyperExtractExtractor extracts entities — exercising the full
hardened pipeline (P1 gallery + P2 normalize + P3 inference).

No graph write (the per-doc graph write is covered by verify_pdf_doctype_e2e;
this isolates the doc_type inference path).
"""

from __future__ import annotations

import asyncio
import os
from types import SimpleNamespace

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_openai import ChatOpenAI
from pypdf import PdfReader

from arrow_lake.config import HugeGraphConfig
from arrow_lake.knowledge_graph.doc_type_router import (
    DocTypeClassifier,
    DocTypeRouter,
)
from arrow_lake.knowledge_graph.he_extractor import HyperExtractExtractor

PDF = "docs/cookbook/datas/papers/full_text/zh002_向量数据库技术选型与实践.pdf"
OLLAMA = os.getenv("OLLAMA_API_BASE", "http://10.100.93.100:11434/v1")
MODEL = os.getenv("HE_MODEL", "qwen3:30b-a3b")


def first_chunk(path: str) -> str:
    reader = PdfReader(path)
    full = " ".join(" ".join((p.extract_text() or "").split()) for p in reader.pages)
    return full[:1200]


async def main() -> None:
    text = first_chunk(PDF)
    print(f"[pdf] chunk[:120]: {text[:120]!r}")

    cfg = HugeGraphConfig(
        enabled=True, host="localhost", port=8089,
        extractor_backend="he", he_model=MODEL,
    )
    router = DocTypeRouter(cfg.he_doc_type_templates, cfg.he_default_template)

    # Classifier LLM: reuse the he_extractor's ChatOpenAI pattern (qwen3/Ollama).
    # max_tokens large enough for qwen3 (a thinking model) to emit the label
    # after its reasoning; the classifier scans the full response.
    chat = ChatOpenAI(
        model=MODEL, api_key="dummy", base_url=OLLAMA, temperature=0, max_tokens=64,
    )

    async def llm_complete(system: str, user: str) -> str:
        resp = await chat.ainvoke([SystemMessage(system), HumanMessage(user)])
        return resp.content

    classifier = DocTypeClassifier(llm_complete)
    extractor = HyperExtractExtractor(
        SimpleNamespace(model=MODEL, api_key="dummy", api_base=OLLAMA),
        doc_type_router=router, model=MODEL, doc_type_classifier=classifier,
    )

    # 1. Explicit inference preview
    inferred = await classifier.classify(text)
    path, source = router.resolve_with_source(inferred)
    print(f"\n[classify] inferred doc_type={inferred!r} (no doc_type passed)")
    print(f"[router]   template={path!r} via {source!r}")

    # 2. Full extraction with doc_type=None (extractor infers internally)
    print(f"\n[he] extracting with doc_type=None via {MODEL} ...")
    result = await extractor.extract(text, chunk_id="e2e", doc_type=None)
    print(f"[he] entities={len(result.entities)} relations={len(result.relations)}")
    for e in result.entities[:12]:
        print(f"     - {e.entity_type:14s} {e.name}")

    assert inferred == "paper", f"expected 'paper', got {inferred!r}"
    assert source in ("override", "gallery"), f"expected routed, got {source!r}"
    assert len(result.entities) > 0, "no entities extracted"
    print("\n✅ P3 e2e passed: doc_type inferred from content -> routed -> extracted")


if __name__ == "__main__":
    asyncio.run(main())
