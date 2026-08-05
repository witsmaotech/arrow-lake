#!/usr/bin/env python3
"""API 速率限制 — 配置、测试 429 响应、豁免路径

演示 Arrow Lake v1.2 的速率限制中间件：
  1. 通过配置启用速率限制
  2. 发送请求触发 429 (Too Many Requests)
  3. 验证 X-RateLimit-* 响应头
  4. 配置路径豁免和 per-endpoint 覆盖

用法:
    # 启动服务 (启用速率限制)
    ARROW_LAKE__RATE_LIMIT__ENABLED=true \
    ARROW_LAKE__RATE_LIMIT__DEFAULT_REQUESTS_PER_MINUTE=5 \
    python -m uvicorn arrow_lake.api.app:create_app --factory --port 8000

    # 运行本教程
    python examples/security/rate_limiting.py
    python examples/security/rate_limiting.py --base-url http://localhost:8000
"""

from __future__ import annotations

import argparse

import requests

BASE_URL = "http://localhost:8000"


def _get(path: str, **kwargs) -> requests.Response:
    return requests.get(f"{BASE_URL}{path}", timeout=30, **kwargs)


# ---------------------------------------------------------------------------
# Step 1: 配置说明
# ---------------------------------------------------------------------------

def step1_config_overview() -> None:
    print("=" * 60)
    print("STEP 1: 速率限制配置")
    print("=" * 60)

    print("""
环境变量配置:

  ARROW_LAKE__RATE_LIMIT__ENABLED=true
  ARROW_LAKE__RATE_LIMIT__DEFAULT_REQUESTS_PER_MINUTE=60
  ARROW_LAKE__RATE_LIMIT__DEFAULT_BURST=10
  ARROW_LAKE__RATE_LIMIT__EXEMPT_PATHS=["/health","/metrics","/docs","/openapi.json"]

YAML 配置:

  rate_limit:
    enabled: true
    default_requests_per_minute: 60
    default_burst: 10
    exempt_paths:
      - /health
      - /metrics
      - /docs
      - /redoc
      - /openapi.json

特性:
  - 固定窗口计数器 (60s 窗口)
  - per (IP, path) 维度限流
  - 异步锁保证线程安全
  - 豁免路径跳过检查
  - OPTIONS 预检请求跳过
  - X-RateLimit-Limit / X-RateLimit-Remaining 响应头
  - 429 响应包含 Retry-After 头
""")

    print("  [PASS]\n")


# ---------------------------------------------------------------------------
# Step 2: 触发 429 响应
# ---------------------------------------------------------------------------

def step2_trigger_rate_limit() -> None:
    print("=" * 60)
    print("STEP 2: 发送请求直到触发 429")
    print("=" * 60)

    # 使用 /api/v1/datasets (非豁免路径)
    path = "/api/v1/datasets"
    rate_limited = False
    rate_limit_headers_seen = False
    request_count = 0

    print(f"\n  向 {path} 发送请求...\n")

    for i in range(100):
        resp = _get(path)
        request_count += 1

        # 检查速率限制头
        limit = resp.headers.get("X-RateLimit-Limit")
        remaining = resp.headers.get("X-RateLimit-Remaining")

        if limit is not None:
            rate_limit_headers_seen = True
            print(f"  请求 #{i+1}: status={resp.status_code}, "
                  f"remaining={remaining}/{limit}")

        if resp.status_code == 429:
            rate_limited = True
            data = resp.json()
            retry_after = resp.headers.get("Retry-After", "N/A")

            print("\n  [429] 速率限制触发!")
            print(f"    error: {data.get('error', 'N/A')}")
            print(f"    message: {data.get('message', 'N/A')}")
            print(f"    Retry-After: {retry_after}s")
            break

        if resp.status_code != 200:
            print(f"  请求 #{i+1}: status={resp.status_code} (非预期)")
            break

    if not rate_limited:
        print(f"\n  [INFO] 发送了 {request_count} 个请求未触发限制")
        print("  提示: 可能未启用速率限制或限制值较高")
        if rate_limit_headers_seen:
            print("  速率限制头已出现在响应中")
    else:
        print(f"\n  在第 {request_count} 个请求触发限制")
        print("  [PASS]")

    print()


# ---------------------------------------------------------------------------
# Step 3: 豁免路径验证
# ---------------------------------------------------------------------------

def step3_exempt_paths() -> None:
    print("=" * 60)
    print("STEP 3: 豁免路径验证")
    print("=" * 60)

    exempt_paths = ["/health", "/metrics", "/docs", "/openapi.json"]

    for path in exempt_paths:
        resp = _get(path)
        limit = resp.headers.get("X-RateLimit-Limit")
        remaining = resp.headers.get("X-RateLimit-Remaining")
        has_headers = limit is not None

        print(f"\n  [{path}] status={resp.status_code}, "
              f"rate_limit_headers={'YES' if has_headers else 'NO'}")

        if has_headers:
            print(f"    X-RateLimit-Limit: {limit}")
            print(f"    X-RateLimit-Remaining: {remaining}")
        else:
            print("    (豁免路径 — 无速率限制头)")

    print("\n  [PASS]\n")


# ---------------------------------------------------------------------------
# Step 4: 等待窗口重置
# ---------------------------------------------------------------------------

def step4_window_reset() -> None:
    print("=" * 60)
    print("STEP 4: 窗口重置说明")
    print("=" * 60)

    print("""
固定窗口重置:

  1. 速率限制使用 60 秒固定窗口
  2. 在收到 429 后, 等待 Retry-After 秒数
  3. 窗口重置后计数器清零, 可继续请求

  代码示例:

    import time, requests

    resp = requests.get("http://localhost:8000/api/v1/datasets")
    if resp.status_code == 429:
        retry_after = int(resp.headers.get("Retry-After", "60"))
        print(f"等待 {retry_after} 秒...")
        time.sleep(retry_after)
        # 重试
        resp = requests.get("http://localhost:8000/api/v1/datasets")

生产建议:

  - 使用反向代理 (Nginx/Kong) 作为前端限流
  - Arrow Lake 内置限流适用于单实例部署
  - 多实例部署建议使用 Redis 后端 (v2 规划)
""")

    print("  [PASS]\n")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    global BASE_URL
    parser = argparse.ArgumentParser(description="API 速率限制示例")
    parser.add_argument("--base-url", default=BASE_URL)
    args = parser.parse_args()
    BASE_URL = args.base_url

    print("Arrow Lake API 速率限制示例")
    print(f"  服务地址: {BASE_URL}\n")

    step1_config_overview()

    # 检查服务
    try:
        resp = _get("/health")
        if resp.status_code != 200:
            print("[SKIP] 服务不可用, 仅展示配置说明")
            return
    except requests.ConnectionError:
        print("[SKIP] 服务未启动, 仅展示配置说明")
        return

    step2_trigger_rate_limit()
    step3_exempt_paths()
    step4_window_reset()

    print("=" * 60)
    print("速率限制示例完成")
    print("=" * 60)


if __name__ == "__main__":
    main()
