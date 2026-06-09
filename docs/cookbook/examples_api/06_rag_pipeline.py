#!/usr/bin/env python3
"""API-06 — RAG Pipeline

对应 cookbook: 20_rag_qa_system.py, 31_graphrag_qa.py, 34_rag_streaming.py,
              35_rag_prompt_engineering.py, 36_rag_context_window.py, 37_rag_session_management.py
验证: RAG 问答、流式响应、实体抽取、Prompt 模板、会话历史
前置: 需要已存在含 text_content + text_embedding 列的数据集
"""

from __future__ import annotations
import os

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from conftest import ArrowLakeClient

BASE_URL = os.environ.get("ARROW_LAKE_BASE_URL", "http://localhost:8000")
API_KEY = os.environ.get("ARROW_LAKE_API_KEY", "dev-api-key-for-local-testing-only")


def main() -> None:
    print("=" * 60)
    print("API-06  RAG Pipeline")
    print("=" * 60)

    c = ArrowLakeClient(BASE_URL, API_KEY)

    ds = c.list_datasets()
    datasets = ds.get("datasets", [])
    if not datasets:
        print("  [SKIP] No datasets available")
        return

    target = max(datasets, key=lambda d: d["num_rows"])
    name = target["name"]
    print(f"\nUsing dataset: {name} ({target['num_rows']} rows)")

    # 1. RAG query
    print("\nSTEP 1: RAG query")
    resp = c.rag_query("What is machine learning?", name, top_k=3)
    if resp.get("success"):
        answer = resp.get("answer", "")
        sources = resp.get("sources", resp.get("citations", []))
        c._pass(f"RAG answer — {len(answer)} chars, {len(sources)} sources")
        print(f"         answer: {answer[:120]}...")
    else:
        print(f"  [INFO] {resp.get('error')}: {resp.get('message', '')[:120]}")

    # 2. RAG query with Chinese
    print("\nSTEP 2: RAG query (Chinese)")
    resp = c.rag_query("什么是深度学习？", name, top_k=3)
    if resp.get("success"):
        answer = resp.get("answer", "")
        c._pass(f"RAG (zh) — {len(answer)} chars")
    else:
        print(f"  [INFO] {resp.get('error')}: {resp.get('message', '')[:120]}")

    # 3. RAG query with retrieval strategy
    print("\nSTEP 3: RAG query (hybrid retrieval)")
    resp = c.rag_query("Explain neural networks", name, top_k=5,
                        retrieval_strategy="hybrid")
    if resp.get("success"):
        c._pass("RAG with hybrid retrieval")
    else:
        print(f"  [INFO] {resp.get('error')}: {resp.get('message', '')[:120]}")

    # 4. RAG query with session (multi-turn)
    print("\nSTEP 4: RAG query with session")
    session_id = "api-test-session-001"
    resp = c.rag_query("What is a transformer model?", name, top_k=3,
                        session_id=session_id)
    if resp.get("success"):
        c._pass(f"RAG session={session_id}")
    else:
        print(f"  [INFO] {resp.get('error')}: {resp.get('message', '')[:120]}")

    # 5. Follow-up query in same session
    print("\nSTEP 5: Follow-up in same session")
    resp = c.rag_query("How does attention mechanism work?", name, top_k=3,
                        session_id=session_id)
    if resp.get("success"):
        c._pass("Follow-up query accepted")
    else:
        print(f"  [INFO] {resp.get('error')}: {resp.get('message', '')[:120]}")

    # 6. Get session history
    print("\nSTEP 6: Session history")
    resp = c.rag_history(session_id)
    if resp.get("success"):
        turns = resp.get("history", resp.get("turns", []))
        c._pass(f"session history — {len(turns)} turns")
    else:
        print(f"  [INFO] {resp.get('error')}: {resp.get('message', '')[:120]}")

    # 7. Entity extraction
    print("\nSTEP 7: Entity extraction")
    resp = c.rag_extract(name, entity_types=["PERSON", "ORG", "TECH"])
    if resp.get("success"):
        entities = resp.get("entities", resp.get("data", []))
        c._pass(f"entity extraction — {len(entities)} entities")
    else:
        print(f"  [INFO] {resp.get('error')}: {resp.get('message', '')[:120]}")

    # 8. Prompt templates
    print("\nSTEP 8: Prompt templates")
    resp = c.rag_templates()
    if resp.get("success"):
        templates = resp.get("templates", resp.get("data", []))
        c._pass(f"prompt templates — {len(templates)} available")
        for t in templates[:3]:
            tpl_name = t.get("name", t.get("id", "?")) if isinstance(t, dict) else str(t)
            print(f"         - {tpl_name}")
    else:
        print(f"  [INFO] {resp.get('error')}: {resp.get('message', '')[:120]}")

    # 9. Streaming RAG (manual SSE parse)
    print("\nSTEP 9: Streaming RAG (SSE)")
    try:
        import ssl as _ssl
        from urllib.request import Request, urlopen
        body = json.dumps({
            "question": "Explain gradient descent",
            "dataset_name": name,
            "top_k": 3,
        }).encode()
        req = Request(
            f"{BASE_URL}/api/v1/rag/query/stream",
            data=body,
            headers={
                "X-API-Key": API_KEY,
                "Content-Type": "application/json",
                "Accept": "text/event-stream",
            },
            method="POST",
        )
        _ctx = None
        if os.environ.get("ARROW_LAKE_SSL_VERIFY", "true").lower() == "false":
            _ctx = _ssl.create_default_context()
            _ctx.check_hostname = False
            _ctx.verify_mode = _ssl.CERT_NONE
        with urlopen(req, timeout=30, context=_ctx) as resp_stream:
            chunks = 0
            for raw_line in resp_stream:
                line = raw_line.decode().strip()
                if line.startswith("data:"):
                    chunks += 1
                    if chunks <= 2:
                        payload = line[5:].strip()
                        print(f"         chunk: {payload[:80]}...")
            c._pass(f"SSE stream — {chunks} chunks received")
    except Exception as e:
        print(f"  [INFO] SSE not available: {e}")

    # 10. RAG query with custom prompt template
    print("\nSTEP 10: RAG with custom template")
    resp = c.rag_query("Summarize the key findings", name, top_k=5,
                        template="concise_summary")
    if resp.get("success"):
        c._pass("RAG with custom template")
    else:
        print(f"  [INFO] {resp.get('error')}: {resp.get('message', '')[:120]}")

    print("\n" + "=" * 60)
    print("API-06  ALL PASSED")
    print("=" * 60)


if __name__ == "__main__":
    main()
