#!/usr/bin/env python3
"""50 — 个人令牌与 system_db 控制面 (v1.9.0)

场景: Admin 签发 personal token, 用户以 ``X-API-Key`` 携带令牌调用 ``/me/*`` 端点
(保存的查询 / 偏好 / 通知)。system_db (libSQL) 是控制面存储, 数据面 (Lance/HugeGraph)
完全不受影响。

教学点:
  1. system_db (libSQL/Turso) = 控制面: RBAC / 身份 / personal_token / catalog 元数据 /
     任务历史 / RAG 会话 / 血缘索引 / 治理 —— 与数据面 (Lance) 物理隔离
  2. personal token 是长生命周期 API 凭证 (X-API-Key), 区别于短时 JWT
  3. /me/* 端点必须用 personal token (JWT/api_key 调不通, user_state.py 硬约束)
  4. system_db 不可用时 fail-close (401) —— RBAC 全依赖 sqld

注意: 本能力是 REST-only —— SDK (Lake) 不签发令牌。用 stdlib urllib, 无第三方依赖。
"""

from __future__ import annotations

import argparse
import json
import urllib.error
import urllib.request
from typing import Any

_DEFAULT_API = "http://127.0.0.1:8000"
# ADMIN 凭证 (dev 环境); 生产请用环境变量, 勿硬编码
_ADMIN_KEY = "dev-api-key-for-local-testing-only"


def _api(method: str, path: str, *, api_key: str = _ADMIN_KEY,
         base: str = _DEFAULT_API, body: dict | None = None) -> Any:
    """最小 REST 调用器 (stdlib urllib, 零依赖)。

    所有写操作用 X-API-Key 鉴权 (personal token 或 admin api_key)。
    """
    url = f"{base}/api/v1{path}"
    data = json.dumps(body).encode() if body else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("X-API-Key", api_key)
    req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            return json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        payload = e.read().decode()[:300]
        return {"_http_error": e.code, "_body": payload}
    except urllib.error.URLError as e:
        return {"_url_error": str(e.reason)}


def main() -> None:
    parser = argparse.ArgumentParser(description="50_personal_token_and_system_db.py")
    parser.add_argument("--api", default=_DEFAULT_API)
    parser.add_argument("--admin-key", default=_ADMIN_KEY)
    args = parser.parse_args()
    print("=" * 64)
    print("50 个人令牌与 system_db 控制面 (v1.9.0)")
    print("=" * 64)

    base = args.api

    # --- Step 1: system_db 架构说明 ---
    print("\n--- Step 1: system_db (libSQL) 控制面职责 ---")
    print("  控制面 (system_db, 独立 sqld 容器):")
    print("    - RBAC / 身份 / personal_token")
    print("    - catalog 元数据 (dataset/schema 注册)")
    print("    - 任务历史 (kg_build / ingest / export)")
    print("    - RAG 会话历史 (session_store)")
    print("    - 血缘索引 / 审计 / 治理")
    print("  数据面 (Lance / HugeGraph / MinIO): 完全独立, 不受 system_db 影响")
    print("  迁移: system_db/migrations/V00x__*.sql (idempotent, 启动自动跑)")

    # --- Step 2: 列出用户 (admin) ---
    print("\n--- Step 2: 列出用户 (GET /admin/users) ---")
    users = _api("GET", "/admin/users", api_key=args.admin_key, base=base)
    if "_http_error" in users or "_url_error" in users:
        print(f"  不可达: {users}")
        print("  启动: docker compose -f deploy/docker-compose.prod_minimal.yml up -d api")
        print("  并确保 SYSTEM_DB_ENABLED=true (deploy/.env)")
        return
    user_list = users.get("users", users) if isinstance(users, dict) else users
    if isinstance(user_list, list):
        print(f"  共 {len(user_list)} 个用户:")
        for u in user_list[:5]:
            print(f"    id={u.get('id', '?')} username={u.get('username', '?')} "
                  f"role={u.get('role', '?')}")
    else:
        print(f"  响应: {str(users)[:200]}")

    # --- Step 3: Admin 签发 personal token ---
    print("\n--- Step 3: 签发 personal token (POST /admin/users/{id}/tokens) ---")
    print("  personal token vs JWT:")
    print("    JWT     → 短时 (access token), Authorization: Bearer <jwt>")
    print("    personal→ 长生命周期, X-API-Key: <token>, 适合脚本/CI")
    # 取第一个非 admin 用户演示 (真实场景按 username 查)
    target_uid = None
    if isinstance(user_list, list):
        for u in user_list:
            if u.get("role") != "ADMIN":
                target_uid = u.get("id")
                break
    if target_uid is None:
        print("  (无可用非 admin 用户; 跳过实际签发, 见下方契约说明)")
    else:
        tok = _api("POST", f"/admin/users/{target_uid}/tokens",
                    api_key=args.admin_key, base=base,
                    body={"name": "cookbook-demo", "scopes": ["me:read", "me:write"]})
        if "_http_error" in tok:
            print(f"  签发 (可能后端未实现该端点, 见 memory admin gap): {tok}")
        else:
            print(f"  签发成功: {str(tok)[:200]}")
            token_value = tok.get("token") or tok.get("key") or tok.get("api_key")
            if token_value:
                _demo_me_endpoints(base, token_value)
                return
    # 契约说明 (端点未实现时)
    print("\n  契约 (POST /admin/users/{id}/tokens):")
    print("    body: {name: str, scopes: [str], expires_in_days?: int}")
    print("    resp: {token: str, id: str, created_at: str}")

    # --- Step 4: /me/* 端点说明 (personal token 鉴权) ---
    print("\n--- Step 4: /me/* 端点 (需 personal token, JWT 调不通) ---")
    _demo_me_endpoints(base, "<your-personal-token>")


def _demo_me_endpoints(base: str, token: str) -> None:
    """用 personal token 调 /me/* (saved-queries / preferences / notifications)。"""
    print("\n  --- 用 personal token 调 /me/* ---")
    # 保存的查询
    r = _api("GET", "/me/saved-queries", api_key=token, base=base)
    print(f"  GET /me/saved-queries → {str(r)[:160]}")
    # 偏好
    r = _api("GET", "/me/preferences", api_key=token, base=base)
    print(f"  GET /me/preferences   → {str(r)[:160]}")
    # 通知
    r = _api("GET", "/me/notifications", api_key=token, base=base)
    print(f"  GET /me/notifications → {str(r)[:160]}")
    print("  → /me/* 硬约束 personal token (user_state.py); JWT/api_key 返 401/403")
    print("  → system_db 不可用时 fail-close: 全部 401 (RBAC 无后端可查)")


if __name__ == "__main__":
    main()
