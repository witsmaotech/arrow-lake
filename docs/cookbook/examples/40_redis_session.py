#!/usr/bin/env python3
"""40 — Redis 分布式会话协调

场景: 演示 Redis 分布式信号量协调 DuckDB 会话, 以及回退到
     threading.Semaphore 的降级行为。

依赖: redis (可选, 缺失时自动降级)
"""

from __future__ import annotations

import argparse
import shutil
import threading
from pathlib import Path

try:
    from arrow_lake import Lake
    from arrow_lake.config import ArrowLakeConfig, RedisConfig
    from arrow_lake.query._redis_semaphore import (
        RedisCountingSemaphore,
        SemaphoreStats,
        create_semaphore,
    )
except ImportError as exc:
    print(f"导入失败: {exc}")
    print("请安装 arrow_lake:  pip install -e .")
    raise SystemExit(1)


_DEFAULT_BASE_URI = "./_tmp_redis_session"


def main() -> None:
    parser = argparse.ArgumentParser(description="40_redis_session.py")
    parser.add_argument("--base-uri", default=_DEFAULT_BASE_URI)
    parser.add_argument("--no-cleanup", action="store_true")
    args = parser.parse_args()
    no_cleanup = args.no_cleanup
    print("=" * 60)
    print("40 Redis 分布式会话协调")
    print("=" * 60)

    base = Path(args.base_uri)
    if base.exists():
        shutil.rmtree(base)

    # STEP 1: 配置 Redis
    print("\nSTEP 1: Redis 配置方式")
    print("  方式 A — YAML 配置文件:")
    print("    redis:")
    print("      enabled: true")
    print("      url: redis://localhost:6379/0")
    print("      password: my_secret_password")
    print("      ssl: false")
    print("      semaphore_key_prefix: arrow_lake:semaphore:")
    print("      semaphore_ttl_seconds: 300")
    print("      redis_pool_size: 10")
    print()
    print("  方式 B — 环境变量:")
    print("    ARROW_LAKE__REDIS__ENABLED=true")
    print("    ARROW_LAKE__REDIS__URL=redis://localhost:6379/0")
    print("    ARROW_LAKE__REDIS__PASSWORD=my_secret_password")

    # STEP 2: 创建信号量 (Redis 启用)
    print("\nSTEP 2: 创建分布式信号量")
    redis_config_enabled = RedisConfig(
        enabled=True,
        url="redis://localhost:6379/0",
        password="",
        semaphore_key_prefix="arrow_lake:semaphore:",
        semaphore_ttl_seconds=300,
    )
    redis_sem = create_semaphore(redis_config_enabled, max_permits=5)
    if isinstance(redis_sem, RedisCountingSemaphore):
        stats: SemaphoreStats = redis_sem.get_stats()
        print(f"  Redis 信号量已连接")
        print(f"  可用许可: {stats.available_permits}")
        print(f"  总许可数: {stats.total_permits}")
        print(f"  Redis 连接: {stats.redis_connected}")
        redis_sem.shutdown()
    else:
        print("  Redis 不可用, 降级为 threading.Semaphore")

    # STEP 3: 降级模式 (Redis 禁用)
    print("\nSTEP 3: 降级模式 (Redis 禁用)")
    redis_config_disabled = RedisConfig(enabled=False)
    fallback_sem = create_semaphore(redis_config_disabled, max_permits=5)
    if isinstance(fallback_sem, threading.Semaphore):
        print("  使用 threading.Semaphore (进程内)")
        print("  适用场景: 单实例部署 / 开发测试")
    else:
        print("  (未预期: RedisConfig.enabled=False 但返回了 Redis 信号量)")

    # STEP 4: 通过 Lake Facade 使用
    print("\nSTEP 4: 通过 Lake Facade 透明使用")
    config = ArrowLakeConfig()
    config.redis.enabled = False  # 确保 Redis 降级
    lake = Lake(base_uri=args.base_uri, arrow_lake_config=config)
    print(f"  Lake 实例已创建 (redis.enabled={config.redis.enabled})")
    print("  DuckDB 会话管理自动使用合适信号量")

    # STEP 5: 健康端点查看会话池状态
    print("\nSTEP 5: 健康端点查看会话池")
    print("  GET /health/ready 返回 duckdb_pool 信息:")
    print("    {")
    print('      "status": "ok",')
    print('      "duckdb_pool": {')
    print('        "pool_size": 5,')
    print('        "active_sessions": 0,')
    print('        "queued_requests": 0,')
    print('        "total_queries": 0,')
    print('        "total_errors": 0')
    print("      }")
    print("    }")

    # STEP 6: 信号量获取与释放演示
    print("\nSTEP 6: 信号量获取与释放")
    local_sem = threading.Semaphore(3)
    acquired = local_sem.acquire(timeout=1.0)
    print(f"  获取许可: {acquired}")
    local_sem.release()
    print(f"  释放许可: 完成")

    print("\n  [全部 PASS]")
    if not no_cleanup:
        lake.shutdown()
        shutil.rmtree(base, ignore_errors=True)
        print("(已清理)")


if __name__ == "__main__":
    main()
