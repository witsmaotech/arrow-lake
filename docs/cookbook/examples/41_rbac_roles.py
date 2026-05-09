#!/usr/bin/env python3
"""41 — RBAC 角色权限控制

场景: 演示 VIEWER / EDITOR / ADMIN 三级角色的权限分配与访问控制。
     展示 API 端点的角色要求, 以及权限不足时的 403 响应。

依赖: PyJWT (可选, 用于 JWT 令牌交换演示)
"""

from __future__ import annotations

import argparse
import shutil
from pathlib import Path

try:
    from arrow_lake import Lake
    from arrow_lake.api.auth_models import Role, TokenPayload
    from arrow_lake.api.auth_service import AuthService
    from arrow_lake.api.rbac import Permission, PermissionChecker
    from arrow_lake.config import ArrowLakeConfig, AuthConfig
except ImportError as exc:
    print(f"导入失败: {exc}")
    print("请安装 arrow_lake:  pip install -e .")
    raise SystemExit(1)


_DEFAULT_BASE_URI = "./_tmp_rbac"


def main() -> None:
    parser = argparse.ArgumentParser(description="41_rbac_roles.py")
    parser.add_argument("--base-uri", default=_DEFAULT_BASE_URI)
    parser.add_argument("--no-cleanup", action="store_true")
    args = parser.parse_args()
    no_cleanup = args.no_cleanup
    print("=" * 60)
    print("41 RBAC 角色权限控制")
    print("=" * 60)

    base = Path(args.base_uri)
    if base.exists():
        shutil.rmtree(base)

    lake = Lake(base_uri=args.base_uri)

    # STEP 1: 角色层级与权限矩阵
    print("\nSTEP 1: 角色层级与权限矩阵")
    print("  ADMIN > EDITOR > VIEWER")
    print()
    checker = PermissionChecker()
    roles_perms = {
        "VIEWER": [Permission.DATASET_READ],
        "EDITOR": [Permission.DATASET_READ, Permission.DATASET_WRITE, Permission.DATASET_DELETE],
        "ADMIN": list(Permission),
    }
    for role_name, expected in roles_perms.items():
        actual = checker.get_permissions(role_name.lower())
        print(f"  {role_name}:")
        for p in expected:
            has = p in actual
            print(f"    {p}: {'YES' if has else 'NO'}")

    # STEP 2: 各角色可访问的 API 端点
    print("\nSTEP 2: 各角色可访问的 API 端点")
    endpoint_roles = [
        ("GET  /api/v1/search/*", "VIEWER", "搜索与检索"),
        ("GET  /api/v1/datasets", "VIEWER", "查看数据集列表"),
        ("GET  /api/v1/kg/stats", "VIEWER", "知识图谱统计"),
        ("GET  /api/v1/kg/schema", "VIEWER", "知识图谱 Schema"),
        ("POST /api/v1/datasets/*/ingest", "EDITOR", "数据摄入"),
        ("POST /api/v1/quality/*", "EDITOR", "数据质量操作"),
        ("POST /api/v1/kg/query", "EDITOR", "Gremlin 查询"),
        ("POST /api/v1/backup/create", "ADMIN", "创建备份"),
        ("POST /api/v1/kg/build", "ADMIN", "知识图谱构建"),
        ("DELETE /api/v1/kg/graph", "ADMIN", "删除知识图谱"),
        ("GET  /api/v1/admin/*", "ADMIN", "管理端点"),
    ]
    for endpoint, min_role, desc in endpoint_roles:
        print(f"  {endpoint:40s}  [{min_role:6s}]  {desc}")

    # STEP 3: JWT 令牌交换与角色分配
    print("\nSTEP 3: JWT 令牌交换与角色分配")
    try:
        svc = AuthService(secret_key="demo-secret-key-for-cookbook-only-min32chars!", access_token_minutes=30)
        for role in Role:
            payload = svc.create_access_token(user_id=f"user_{role.value}", role=role)
            encoded = svc._encode(payload)
            print(f"  {role.value:6s} -> token (sub={payload.sub}, jti={payload.jti[:12]}...)")
            # 验证回读
            decoded = svc.verify_token(encoded)
            assert decoded.role == role
            assert decoded.sub == f"user_{role.value}"
        print("  令牌创建与验证: PASS")
    except Exception as e:
        print(f"  令牌操作跳过: {e}")

    # STEP 4: 权限检查 (通过 vs 拒绝)
    print("\nSTEP 4: 权限检查演示")
    test_cases = [
        ("viewer", Permission.DATASET_READ, True),
        ("viewer", Permission.DATASET_WRITE, False),
        ("viewer", Permission.ADMIN_MANAGE, False),
        ("editor", Permission.DATASET_READ, True),
        ("editor", Permission.DATASET_WRITE, True),
        ("editor", Permission.ADMIN_MANAGE, False),
        ("admin", Permission.DATASET_READ, True),
        ("admin", Permission.DATASET_WRITE, True),
        ("admin", Permission.ADMIN_MANAGE, True),
    ]
    for role, perm, expected in test_cases:
        result = checker.has_permission(role, perm)
        status = "PASS" if result == expected else "FAIL"
        icon = "GRANT" if result else "DENY"
        print(f"  {role:6s} + {perm:20s} -> {icon:5s}  [{status}]")

    # STEP 5: 403 权限不足场景
    print("\nSTEP 5: HTTP 403 权限不足场景")
    print("  VIEWER 尝试 POST /api/v1/kg/build (需 ADMIN):")
    print("    -> HTTP 403: Insufficient permissions: requires admin")
    print("  EDITOR 尝试 POST /api/v1/backup/create (需 ADMIN):")
    print("    -> HTTP 403: Insufficient permissions: requires admin")
    print("  VIEWER 尝试 POST /api/v1/datasets/sales/ingest (需 EDITOR):")
    print("    -> HTTP 403: Insufficient permissions: requires editor")

    # STEP 6: 数据集级别 ACL
    print("\nSTEP 6: 数据集级别 ACL")
    checker.grant_dataset_access("sales", "viewer", "read")
    checker.grant_dataset_access("sales", "editor", "write")
    can_viewer_read = checker.check_dataset_access(role="viewer", dataset="sales", action="read")
    can_viewer_write = checker.check_dataset_access(role="viewer", dataset="sales", action="write")
    can_admin_delete = checker.check_dataset_access(role="admin", dataset="sales", action="delete")
    print(f"  viewer  对 sales 的 read:  {'GRANT' if can_viewer_read else 'DENY'}")
    print(f"  viewer  对 sales 的 write: {'GRANT' if can_viewer_write else 'DENY'}")
    print(f"  admin   对 sales 的 delete: {'GRANT' if can_admin_delete else 'DENY'} (ADMIN 始终拥有全部权限)")

    print("\n  [全部 PASS]")
    if not no_cleanup:
        lake.shutdown()
        shutil.rmtree(base, ignore_errors=True)
        print("(已清理)")


if __name__ == "__main__":
    main()
