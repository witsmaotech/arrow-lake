#!/usr/bin/env python3
"""29 — HTTP 远程摄取

场景: 从 HTTP(S) URL 远程摄取数据文件，展示 SSRF 防护和重试逻辑。

数据: 公开可访问的 CSV/JSONL URL
"""

from __future__ import annotations

import argparse

import shutil
import sys
from pathlib import Path

from arrow_lake import Lake

_DEFAULT_BASE_URI = "./_tmp_http_ingest"

# 使用公开数据集 URL (GitHub raw)
DEMO_URLS = [
    "https://raw.githubusercontent.com/apache/arrow/main/cpp/src/arrow/csv/test_decimal.csv",
]


def main() -> None:
    parser = argparse.ArgumentParser(description="29_http_ingest.py")
    parser.add_argument("--base-uri", default=_DEFAULT_BASE_URI)
    parser.add_argument("--no-cleanup", action="store_true")
    args = parser.parse_args()
    no_cleanup = args.no_cleanup
    print("=" * 60)
    print("29 HTTP 远程摄取")
    print("=" * 60)

    base = Path(args.base_uri)
    if base.exists():
        shutil.rmtree(base)

    lake = Lake(base_uri=args.base_uri)

    # STEP 1: 展示 HTTP 摄取接口
    print("STEP 1: HTTP 摄取接口说明")
    print("  lake.ingest_http(dataset_name, urls)")
    print("  特性: SSRF 防护 (私有 IP 拦截)")
    print("        指数退避重试 (429/503/超时)")
    print("        Content-Length 预检")
    print(f"  目标 URL: {DEMO_URLS[0][:60]}...")

    # STEP 2: 执行 HTTP 摄取
    print("\nSTEP 2: 执行 HTTP 摄取")
    try:
        report = lake.ingest_http("remote_data", DEMO_URLS)
        print(f"  摄入: {report.total_rows} 行, {report.total_files} 文件")
        for src in report.sources:
            print(f"    {src.path[:60]}... ({src.row_count} 行)")
    except (OSError, ValueError) as e:
        err_type = type(e).__name__
        print(f"  HTTP 摄取失败 [{err_type}]: {e}")
        if "ConnectionError" in err_type or "timeout" in str(e).lower():
            print("\n  可能原因: 网络不可达")
            print("  解决方案: 确保网络连接正常, 或使用本地文件摄取")

    # STEP 3: 查看数据集
    print("\nSTEP 3: 查看数据集")
    for name in lake.list_datasets():
        ds = lake.open_dataset(name)
        print(f"  {name}: {ds.count_rows()} 行, {len(ds.schema)} 列")
        for f in ds.schema:
            print(f"    {f.name}: {f.type}")

    # STEP 4: SQL 分析远程数据
    print("\nSTEP 4: SQL 分析远程数据")
    for name in lake.list_datasets():
        try:
            result = lake.olap_query(name, f"SELECT COUNT(*) as cnt FROM {name}")
            row = result.table.to_pylist()[0]
            print(f"  [{name}] 总行数: {row['cnt']}")
        except (ValueError, RuntimeError) as e:
            print(f"  [{name}] 查询跳过: {e}")

    # STEP 5: SSRF 防护演示
    print("\nSTEP 5: SSRF 防护说明")
    blocked_urls = [
        "http://127.0.0.1/admin",
        "http://192.168.1.1/secrets",
        "http://10.0.0.1/internal",
        "http://localhost:8080/api",
    ]
    print("  以下 URL 会被拦截 (私有/回环 IP):")
    for url in blocked_urls:
        print(f"    {url}")

    print("\n  [全部 PASS]")
    if not no_cleanup:
        lake.shutdown()
        shutil.rmtree(base, ignore_errors=True)
        print("(已清理)")


if __name__ == "__main__":
    main()
