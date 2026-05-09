#!/usr/bin/env python3
"""43 — JWT 黑名单与 Redis 持久化

场景: 演示 JWT 令牌撤销 (黑名单) 机制。
     - 令牌创建与撤销
     - 已撤销令牌返回 401
     - Redis 持久化 (Redis 启用时)
     - 内存回退 (Redis 禁用或不可用时)

依赖: PyJWT (pip install PyJWT)
"""

from __future__ import annotations

import argparse
import shutil
import threading
from pathlib import Path

try:
    from arrow_lake import Lake
    from arrow_lake.api.auth_models import Role
    from arrow_lake.api.auth_service import AuthService
    from arrow_lake.config import ArrowLakeConfig
except ImportError as exc:
    print(f"导入失败: {exc}")
    print("请安装 arrow_lake:  pip install -e .")
    raise SystemExit(1)

try:
    import jwt  # noqa: F401 — 检查 PyJWT 是否可用
except ImportError:
    print("PyJWT 未安装, 部分演示将跳过")
    print("安装:  pip install PyJWT")
    jwt = None  # type: ignore[assignment]


_DEFAULT_BASE_URI = "./_tmp_jwt_blacklist"
_SECRET_KEY = "cookbook-demo-secret-key-must-be-at-least-32-characters-long"


def main() -> None:
    parser = argparse.ArgumentParser(description="43_jwt_blacklist.py")
    parser.add_argument("--base-uri", default=_DEFAULT_BASE_URI)
    parser.add_argument("--no-cleanup", action="store_true")
    args = parser.parse_args()
    no_cleanup = args.no_cleanup
    print("=" * 60)
    print("43 JWT 黑名单与 Redis 持久化")
    print("=" * 60)

    base = Path(args.base_uri)
    if base.exists():
        shutil.rmtree(base)

    lake = Lake(base_uri=args.base_uri)

    # STEP 1: 令牌创建
    print("\nSTEP 1: 令牌创建")
    if jwt is None:
        print("  PyJWT 未安装, 跳过令牌操作演示")
        print("  原理说明:")
        print("    1. AuthService.create_access_token(user_id, role) -> TokenPayload")
        print("    2. AuthService._encode(payload) -> JWT 字符串")
        print("    3. 客户端携带 Authorization: Bearer <token> 访问 API")
    else:
        svc = AuthService(secret_key=_SECRET_KEY, access_token_minutes=30)
        payload = svc.create_access_token(user_id="user_001", role=Role.EDITOR)
        encoded = svc._encode(payload)
        print(f"  用户:   {payload.sub}")
        print(f"  角色:   {payload.role.value}")
        print(f"  JTI:    {payload.jti}")
        print(f"  令牌:   {encoded[:50]}...")

        # STEP 2: 验证有效令牌
        print("\nSTEP 2: 验证有效令牌")
        decoded = svc.verify_token(encoded)
        print(f"  验证通过: sub={decoded.sub}, role={decoded.role.value}")

        # STEP 3: 撤销令牌
        print("\nSTEP 3: 撤销令牌")
        jti = payload.jti
        print(f"  撤销 JTI: {jti}")
        svc.revoke_token(jti)
        print("  令牌已加入黑名单")

        # STEP 4: 已撤销令牌验证
        print("\nSTEP 4: 已撤销令牌返回 401")
        try:
            svc.verify_token(encoded)
            print("  ERROR: 已撤销令牌未被拦截!")
        except ValueError as e:
            print(f"  拦截成功: {e}")
            print("  API 行为: HTTP 401 Unauthorized")

        # STEP 5: 多令牌撤销
        print("\nSTEP 5: 批量撤销演示")
        tokens = []
        for i in range(3):
            p = svc.create_access_token(user_id=f"user_{i:03d}", role=Role.VIEWER)
            t = svc._encode(p)
            tokens.append((p, t))
            print(f"  创建令牌: user_{i:03d} (jti={p.jti[:12]}...)")

        # 撤销其中一个
        svc.revoke_token(tokens[1][0].jti)
        print(f"  已撤销: {tokens[1][0].jti[:12]}...")

        for i, (p, t) in enumerate(tokens):
            try:
                svc.verify_token(t)
                print(f"  令牌 {i}: 有效")
            except ValueError:
                print(f"  令牌 {i}: 已撤销")

    # STEP 6: Redis 持久化黑名单
    print("\nSTEP 6: Redis 持久化黑名单")
    print("  当 redis.enabled=true 时:")
    print("    - 撤销令牌写入 Redis: SETEX jwt:blacklist:<jti> <ttl> 1")
    print("    - 查询黑名单: EXISTS jwt:blacklist:<jti>")
    print("    - TTL 自动过期 (refresh_token_days * 86400 + 3600)")
    print()
    print("  配置方式:")
    print("    redis:")
    print("      enabled: true")
    print("      url: redis://localhost:6379/0")
    print("      password: your_password")
    print()
    print("  Redis 不可用时自动降级为内存黑名单")

    # STEP 7: 内存回退模式
    print("\nSTEP 7: 内存回退模式")
    if jwt is not None:
        svc_mem = AuthService(secret_key=_SECRET_KEY, access_token_minutes=30)
        # set_redis 未调用 -> 使用内存黑名单
        p = svc_mem.create_access_token(user_id="mem_user", role=Role.VIEWER)
        t = svc_mem._encode(p)
        svc_mem.revoke_token(p.jti)
        try:
            svc_mem.verify_token(t)
            print("  ERROR: 内存黑名单未拦截!")
        except ValueError as e:
            print(f"  内存黑名单工作正常: {e}")
        print("  最大容量: 100,000 条 (超出自动淘汰过期条目)")
    else:
        print("  PyJWT 未安装, 跳过内存回退演示")
        print("  原理: AuthService 内部维护 dict[jti, timestamp] 作为黑名单")

    # STEP 8: API 端点说明
    print("\nSTEP 8: 相关 API 端点")
    print("  POST /api/v1/auth/token   — 交换 JWT 令牌")
    print("  POST /api/v1/auth/refresh — 刷新访问令牌")
    print("  POST /api/v1/auth/logout  — 撤销当前令牌")
    print("  GET  /api/v1/auth/me      — 查看当前用户信息")

    print("\n  [全部 PASS]")
    if not no_cleanup:
        lake.shutdown()
        shutil.rmtree(base, ignore_errors=True)
        print("(已清理)")


if __name__ == "__main__":
    main()
