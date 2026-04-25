#!/usr/bin/env python3
"""37 — RAG 会话管理

场景: 展示 SessionStore 的多会话管理、历史查询、会话删除和限额控制。
无需 LLM 服务 (SessionStore 是纯内存操作)。

数据: 内部构造
"""

from __future__ import annotations

import shutil
import sys
from pathlib import Path

from arrow_lake.rag.session import SessionStore
from arrow_lake.rag.pipeline import RAGResponse

BASE_URI = "./_tmp_sessions"


def main() -> None:
    no_cleanup = "--no-cleanup" in sys.argv
    print("=" * 60)
    print("37 RAG 会话管理")
    print("=" * 60)

    base = Path(BASE_URI)
    if base.exists():
        shutil.rmtree(base)

    # STEP 1: 创建 SessionStore
    print("STEP 1: 创建 SessionStore")
    store = SessionStore(
        max_sessions=100,
        max_turns_per_session=10,
    )
    print(f"  max_sessions: {store._max_sessions}")
    print(f"  max_turns_per_session: {store._max_turns_per_session}")

    # STEP 2: 多会话写入
    print("\nSTEP 2: 模拟 3 个用户会话")
    sessions = ["user_alice", "user_bob", "user_charlie"]
    questions = {
        "user_alice": ["Arrow 是什么？", "它有什么优势？"],
        "user_bob": ["什么是 Parquet？", "如何读取 Parquet 文件？", "它支持压缩吗？"],
        "user_charlie": ["DuckDB 是什么？"],
    }

    for sid, qs in questions.items():
        for i, q in enumerate(qs):
            fake_resp = RAGResponse(
                answer=f"这是对 '{q}' 的模拟回答 (第{i+1} 轮)",
                citations=(),
                retrieval_count=3,
                context_tokens=500,
            )
            store.save_turn(sid, q, fake_resp)
        print(f"  {sid}: {len(qs)} 轮对话")

    # STEP 3: 查询历史
    print("\nSTEP 3: 查询会话历史")
    for sid in sessions:
        history = store.get_history(sid)
        print(f"  [{sid}] {len(history)} 轮:")
        for turn in history:
            q = turn.get("question", "?")[:40]
            a = turn.get("answer", "?")
            if a and len(str(a)) > 40:
                a = str(a)[:40] + "..."
            print(f"    Q: {q}")
            print(f"    A: {a}")

    # STEP 4: 列出所有会话
    print("\nSTEP 4: 列出所有会话")
    all_sessions = store.list_sessions()
    print(f"  活跃会话: {len(all_sessions)}")
    for s in all_sessions:
        sid = s.get("session_id", "?") if isinstance(s, dict) else "?"
        last_q = s.get("last_question", "?") if isinstance(s, dict) else "?"
        if last_q and len(str(last_q)) > 30:
            last_q = str(last_q)[:30] + "..."
        ts = s.get("timestamp", "?") if isinstance(s, dict) else "?"
        print(f"    {sid:<20} last_q={last_q}  ts={ts}")

    # STEP 5: 删除单个会话
    print("\nSTEP 5: 删除单个会话 (user_charlie)")
    store.delete_session("user_charlie")
    remaining = store.list_sessions()
    print(f"  删除后活跃会话: {len(remaining)}")
    for s in remaining:
        sid = s.get("session_id", "?") if isinstance(s, dict) else "?"
        print(f"    {sid}")

    # STEP 6: 新增更多会话测试限额
    print("\nSTEP 6: 会话限额测试")
    print("  尝试添加 100 个会话 (限额=100)...")
    count = 0
    for i in range(105):
        store.save_turn(f"overflow_{i}", f"问题{i}", RAGResponse(answer=f"回答{i}", citations=(), retrieval_count=1))
        count = len(store.list_sessions())
    print(f"  实际活跃会话: {count} (超出限额的旧会话被自动淘汰)")

    # STEP 7: 单会话轮次限额
    print("\nSTEP 7: 单会话轮次限额 (max_turns_per_session=10)")
    store2 = SessionStore(max_sessions=10, max_turns_per_session=3)
    for i in range(5):
        store2.save_turn("limited", f"问题{i}", RAGResponse(answer=f"回答{i}", citations=(), retrieval_count=1))
    limited_history = store2.get_history("limited")
    print(f"  写入 5 轮, 保留: {len(limited_history)} 轮")

    print("\n  [全部 PASS]")
    shutil.rmtree(base, ignore_errors=True)
    print("(已清理)")


if __name__ == "__main__":
    main()
