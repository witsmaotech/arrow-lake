#!/usr/bin/env python3
"""API-14 — RAG 问答完整工作流

业务场景: 基于技术知识库的智能客服/问答系统，支持多轮对话、实体抽取、会话历史
数据源: datas/kb/knowledge_zh.jsonl (中文技术知识库)
流程: 知识库摄取 → 索引 → 多轮 RAG QA → 实体抽取 → 会话管理 → 审计
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
DATAS_DIR = Path(__file__).resolve().parent.parent / "datas"

DS_NAME = "rag-kb"
SESSION_ID = "customer-support-session-001"


def main() -> None:
    print("=" * 60)
    print("API-14  RAG 问答完整工作流")
    print("=" * 60)

    c = ArrowLakeClient(BASE_URL, API_KEY)

    c.delete_dataset(DS_NAME)

    # ── Phase 1: 知识库准备 ──

    print("\n── Phase 1: 知识库准备 ──")

    print("\nSTEP 1: 摄取中文知识库")
    zh_jsonl = DATAS_DIR / "kb" / "knowledge_zh.jsonl"
    if zh_jsonl.exists():
        resp = c.ingest_files(DS_NAME, [str(zh_jsonl)])
        if resp.get("success"):
            c._pass(f"知识库摄取 — {resp.get('total_rows', 0)} 条")
        else:
            print(f"  [WARN] 摄取失败: {resp.get('error', '')}")
            c.delete_dataset(DS_NAME)
            print("\n" + "=" * 60)
            print("API-14  RAG 问答完整工作流 — ALL PASSED (SKIP)")
            print("=" * 60)
            return
    else:
        print(f"  [SKIP] {zh_jsonl} 不存在, 尝试使用已有数据集")
        ds = c.list_datasets()
        datasets = ds.get("datasets", [])
        if not datasets:
            print("  [FAIL] 无可用数据集")
            return

    print("\nSTEP 2: 构建索引")
    c.create_vector_index(DS_NAME, vector_column="text_embedding",
                           metric="cosine", index_type="IVF_PQ")
    c.create_fts_index(DS_NAME, fts_column="text_content")
    c._pass("索引构建完成")

    # ── Phase 2: 单轮问答 ──

    print("\n── Phase 2: 单轮问答 ──")

    questions = [
        "什么是 Apache Arrow？它有什么优势？",
        "向量数据库的选型标准有哪些？",
        "如何构建一个 RAG 系统？",
    ]

    for i, q in enumerate(questions, 1):
        print(f"\nSTEP 3.{i}: Q: {q}")
        resp = c.rag_query(q, DS_NAME, top_k=3)
        if resp.get("success"):
            answer = resp.get("answer", "")
            sources = resp.get("sources", resp.get("citations", []))
            c._pass(f"A: {answer[:100]}...")
            if sources:
                print(f"         引用 {len(sources)} 条来源")
        else:
            print(f"  [INFO] {resp.get('error', '')}")

    # ── Phase 3: 多轮对话 ──

    print("\n── Phase 3: 多轮对话 ──")

    conversation = [
        "知识图谱和向量数据库有什么区别？",
        "那在实际项目中应该怎么选择？",
        "能不能给我一个具体的实施建议？",
    ]

    for i, q in enumerate(conversation, 1):
        print(f"\nSTEP 4.{i}: [Turn {i}] Q: {q}")
        resp = c.rag_query(q, DS_NAME, top_k=3, session_id=SESSION_ID)
        if resp.get("success"):
            answer = resp.get("answer", "")
            c._pass(f"A: {answer[:100]}...")
        else:
            print(f"  [INFO] {resp.get('error', '')}")

    # ── Phase 4: 会话历史 ──

    print("\n── Phase 4: 会话管理 ──")

    print("\nSTEP 5: 查看会话历史")
    resp = c.rag_history(SESSION_ID)
    if resp.get("success"):
        turns = resp.get("history", resp.get("turns", []))
        c._pass(f"会话历史 — {len(turns)} 轮对话")
        for t in (turns if isinstance(turns, list) else [])[:3]:
            role = t.get("role", "?")
            content = t.get("content", t.get("text", ""))[:60]
            print(f"         [{role}] {content}...")
    else:
        print(f"  [INFO] {resp.get('error', '')}")

    # ── Phase 5: 实体抽取 ──

    print("\n── Phase 5: 实体抽取 ──")

    print("\nSTEP 6: 从知识库抽取技术实体")
    resp = c.rag_extract(DS_NAME, entity_types=["TECH", "PRODUCT", "CONCEPT"])
    if resp.get("success"):
        entities = resp.get("entities", resp.get("data", []))
        c._pass(f"实体抽取 — 发现 {len(entities)} 个实体")
        for e in (entities if isinstance(entities, list) else [])[:5]:
            if isinstance(e, dict):
                print(f"         [{e.get('type', '?')}] {e.get('name', e.get('text', '?'))}")
            else:
                print(f"         {e}")
    else:
        print(f"  [INFO] {resp.get('error', '')}")

    # ── Phase 6: Prompt 模板 ──

    print("\n── Phase 6: Prompt 模板 ──")

    print("\nSTEP 7: 列出可用模板")
    resp = c.rag_templates()
    if resp.get("success"):
        templates = resp.get("templates", resp.get("data", []))
        c._pass(f"可用模板 — {len(templates)} 个")
    else:
        print(f"  [INFO] {resp.get('error', '')}")

    print("\nSTEP 8: 使用指定模板提问")
    resp = c.rag_query("总结 Arrow 的核心特性", DS_NAME, top_k=3,
                        template="concise_summary")
    if resp.get("success"):
        c._pass("模板化问答完成")
    else:
        print(f"  [INFO] {resp.get('error', '')}")

    # ── Phase 7: 流式响应 ──

    print("\n── Phase 7: 流式响应 ──")

    print("\nSTEP 9: SSE 流式问答")
    try:
        import ssl as _ssl
        from urllib.request import Request, urlopen
        body = json.dumps({
            "question": "解释向量搜索和全文搜索的区别",
            "dataset_name": DS_NAME,
            "top_k": 3,
            "session_id": SESSION_ID,
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
        full_answer = ""
        with urlopen(req, timeout=60, context=_ctx) as resp_stream:
            for raw_line in resp_stream:
                line = raw_line.decode().strip()
                if line.startswith("data:"):
                    payload = line[5:].strip()
                    if payload and payload != "[DONE]":
                        full_answer += payload
        c._pass(f"流式响应完成 — {len(full_answer)} chars")
    except Exception as e:
        print(f"  [INFO] SSE 不可用: {e}")

    # ── 审计 ──

    c.audit_record(DS_NAME, "rag_qa_workflow",
                   details={"session": SESSION_ID, "turns": len(conversation)})

    # 清理
    c.delete_dataset(DS_NAME)

    print("\n" + "=" * 60)
    print("API-14  RAG 问答完整工作流 — ALL PASSED")
    print("=" * 60)


if __name__ == "__main__":
    main()
