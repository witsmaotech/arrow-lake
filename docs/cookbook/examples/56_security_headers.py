#!/usr/bin/env python3
"""安全响应头 — HSTS, CSP, X-Frame-Options, X-Content-Type-Options

演示 Arrow Lake v1.2 的安全响应头中间件：
  1. 默认安全头列表
  2. 自定义 CSP (Content-Security-Policy)
  3. 配置 X-Frame-Options (DENY / SAMEORIGIN)
  4. 禁用安全头

用法:
    # 启动服务
    python -m uvicorn arrow_lake.api.app:create_app --factory --port 8000

    # 运行本教程
    python examples/security/security_headers.py
    python examples/security/security_headers.py --base-url http://localhost:8000
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
    print("STEP 1: 安全响应头配置")
    print("=" * 60)

    print("""
环境变量配置:

  ARROW_LAKE__API__SECURITY_HEADERS_ENABLED=true   (默认启用)
  ARROW_LAKE__API__CONTENT_SECURITY_POLICY=         (空=不设置)
  ARROW_LAKE__API__FRAME_OPTIONS=DENY               (DENY|SAMEORIGIN)

YAML 配置:

  api:
    security_headers_enabled: true
    content_security_policy: ""
    frame_options: "DENY"

默认安全头:

  Header                         Value
  ─────────────────────────────────────────────────────────
  Strict-Transport-Security      max-age=31536000; includeSubDomains
  X-Content-Type-Options         nosniff
  X-Frame-Options                DENY
  Referrer-Policy                strict-origin-when-cross-origin
  Permissions-Policy             camera=(), microphone=(), geolocation=()

跳过路径:
  /health, /metrics — 不添加安全头 (避免暴露安全配置)
""")

    print("  [PASS]\n")


# ---------------------------------------------------------------------------
# Step 2: 验证默认安全头
# ---------------------------------------------------------------------------

def step2_verify_headers() -> None:
    print("=" * 60)
    print("STEP 2: 验证默认安全头")
    print("=" * 60)

    expected_headers = {
        "Strict-Transport-Security": "max-age=31536000",
        "X-Content-Type-Options": "nosniff",
        "X-Frame-Options": "DENY",
        "Referrer-Policy": "strict-origin-when-cross-origin",
        "Permissions-Policy": "camera=()",
    }

    resp = _get("/api/v1/datasets")
    print(f"\n[GET /api/v1/datasets] status={resp.status_code}\n")

    found = 0
    for header, expected_value in expected_headers.items():
        actual = resp.headers.get(header, "(missing)")
        match = expected_value in actual
        status = "OK" if match else "MISS"
        print(f"  {header}: {actual} [{status}]")
        if match:
            found += 1

    print(f"\n  找到 {found}/{len(expected_headers)} 个安全头")

    if found == len(expected_headers):
        print("  [PASS] 全部安全头就绪")
    elif found == 0:
        print("  [INFO] 安全头未启用 (security_headers_enabled=false)")
    else:
        print("  [WARN] 部分安全头缺失")

    print()


# ---------------------------------------------------------------------------
# Step 3: 豁免路径验证
# ---------------------------------------------------------------------------

def step3_exempt_paths() -> None:
    print("=" * 60)
    print("STEP 3: 豁免路径 (health/metrics)")
    print("=" * 60)

    exempt_paths = ["/health", "/metrics"]
    api_path = "/api/v1/datasets"

    print()

    for path in exempt_paths:
        resp = _get(path)
        has_hsts = "Strict-Transport-Security" in resp.headers
        print(f"  [{path}] HSTS={'YES' if has_hsts else 'NO (豁免)'}")

    resp = _get(api_path)
    has_hsts = "Strict-Transport-Security" in resp.headers
    print(f"  [{api_path}] HSTS={'YES' if has_hsts else 'NO'}")

    print("\n  [PASS]\n")


# ---------------------------------------------------------------------------
# Step 4: 自定义配置示例
# ---------------------------------------------------------------------------

def step4_custom_config() -> None:
    print("=" * 60)
    print("STEP 4: 自定义安全头配置")
    print("=" * 60)

    print("""
场景 1: 嵌入式 iframe (需要 SAMEORIGIN)

  ARROW_LAKE__API__FRAME_OPTIONS=SAMEORIGIN

场景 2: 启用 CSP

  ARROW_LAKE__API__CONTENT_SECURITY_POLICY= \\
    "default-src 'self'; script-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net; \\
     style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; \\
     img-src 'self' data: https:; font-src 'self' https://fonts.gstatic.com; \\
     connect-src 'self' https://*.example.com; frame-src 'none'; object-src 'none'"

场景 3: 开发环境禁用安全头

  ARROW_LAKE__API__SECURITY_HEADERS_ENABLED=false

代码中检查安全头:

  import requests

  resp = requests.get("http://localhost:8000/api/v1/datasets")
  headers = dict(resp.headers)

  assert "X-Content-Type-Options" in headers
  assert headers["X-Content-Type-Options"] == "nosniff"
  assert "Strict-Transport-Security" in headers
  assert "X-Frame-Options" in headers
""")

    print("  [PASS]\n")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    global BASE_URL
    parser = argparse.ArgumentParser(description="安全响应头示例")
    parser.add_argument("--base-url", default=BASE_URL)
    args = parser.parse_args()
    BASE_URL = args.base_url

    print("Arrow Lake 安全响应头示例")
    print(f"  服务地址: {BASE_URL}\n")

    step1_config_overview()

    try:
        resp = _get("/health")
        if resp.status_code != 200:
            print("[SKIP] 服务不可用, 仅展示配置说明")
            return
    except requests.ConnectionError:
        print("[SKIP] 服务未启动, 仅展示配置说明")
        return

    step2_verify_headers()
    step3_exempt_paths()
    step4_custom_config()

    print("=" * 60)
    print("安全响应头示例完成")
    print("=" * 60)


if __name__ == "__main__":
    main()
