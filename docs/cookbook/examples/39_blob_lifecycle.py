#!/usr/bin/env python3
"""39 — Blob 生命周期管理

场景: S3/MinIO 对象的存储分层、Glacier 恢复、成本估算。
通过 LifecycleConfig 配置自动转换规则，管理冷热数据分离。

前置条件:
  - MinIO 运行中 (docker compose --profile core up -d minio minio-init)
  - 或配置 S3 凭据

用法:
    python docs/cookbook/examples/39_blob_lifecycle.py
    python docs/cookbook/examples/39_blob_lifecycle.py --no-cleanup
"""

from __future__ import annotations

import argparse

import shutil
import sys
from pathlib import Path

from arrow_lake import Lake
from arrow_lake.config import ArrowLakeConfig, LifecycleConfig

_DEFAULT_BASE_URI = "./_tmp_lifecycle"


def _make_config(endpoint: str = "http://localhost:9000") -> ArrowLakeConfig:
    return ArrowLakeConfig(
        storage__backend="minio",
        storage__s3_endpoint=endpoint,
        storage__s3_access_key="minioadmin",
        storage__s3_secret_key="minioadmin",
        storage__s3_bucket="arrow-lake",
        lifecycle=LifecycleConfig(
            enabled=True,
            standard_to_ia_days=30,
            ia_to_glacier_days=90,
            glacier_expiration_days=365,
            excluded_prefixes=["thumbnails/", "indices/"],
            glacier_retrieval_tier="Standard",
        ),
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Blob lifecycle management demo")
    parser.add_argument("--base-uri", default=_DEFAULT_BASE_URI)
    parser.add_argument("--no-cleanup", action="store_true")
    parser.add_argument("--endpoint", default="http://localhost:9000")
    args = parser.parse_args()
    endpoint = args.endpoint
    no_cleanup = args.no_cleanup
    endpoint = "http://localhost:9000"

    config = _make_config(endpoint)
    base = Path(args.base_uri)
    if base.exists():
        shutil.rmtree(base)

    print("=" * 60)
    print("39 Blob 生命周期管理")
    print("=" * 60)

    lake = Lake(base_uri=str(base), config=config)
    print(f"  Lake version: {lake.version()}")
    print(f"  Lifecycle enabled: {lake._config.lifecycle.enabled}")
    print(f"  Standard -> IA: {lake._config.lifecycle.standard_to_ia_days} days")
    print(f"  IA -> Glacier: {lake._config.lifecycle.ia_to_glacier_days} days")

    try:
        # STEP 1: 查看当前配置
        print("\n--- STEP 1: 生命周期配置 ---")
        rules = lake.lifecycle_rules()
        print(f"  Enabled: {rules['enabled']}")
        print(f"  Standard -> IA: {rules['standard_to_ia_days']} days")
        print(f"  IA -> Glacier: {rules['ia_to_glacier_days']} days")
        print(f"  Glacier -> Expiration: {rules['glacier_expiration_days']} days")
        print(f"  Excluded: {rules['excluded_prefixes']}")

        # STEP 2: 预览规则
        print("\n--- STEP 2: 预览规则 (不实际应用) ---")
        preview = lake.lifecycle_rules(prefix="demo/")
        print(f"  Prefix: {preview.get('prefix', '(root)')}")
        if preview.get("rules"):
            for rule in preview["rules"]:
                rule_id = rule.get("ID", "")
                transitions = rule.get("Transitions", [])
                desc = ", ".join(
                    f"{t.get('StorageClass')} after {t.get('Days')}d" for t in transitions
                )
                print(f"  Rule: {rule_id} -- {desc}")

        # STEP 3: 成本估算
        print("\n--- STEP 3: 成本估算 ---")
        scenarios = [
            (1000, "STANDARD_IA"),
            (500, "GLACIER"),
            (2000, "DEEP_ARCHIVE"),
        ]
        for size_gb, tier in scenarios:
            est = lake.lifecycle_estimate(total_size_gb=size_gb, target_tier=tier)
            print(f"  {size_gb}GB -> {tier}:")
            print(f"    Monthly savings: ${est['monthly_savings']} ({est['savings_percent']}%)")
            print(f"    Current: ${est['current_monthly_cost']}/mo -> Target: ${est['target_monthly_cost']}/mo")

        print("  [PASS]\n")

    finally:
        if not no_cleanup:
            try:
                shutil.rmtree(base)
                print("  清理完成")
            except OSError:
                pass

    print("=" * 60)
    print("  示例 39 完成!")
    print("=" * 60)
    print("""
CLI 等价命令:

  arrow-lake lifecycle config          # 查看配置
  arrow-lake lifecycle rules           # 预览规则
  arrow-lake lifecycle apply           # 应用规则
  arrow-lake lifecycle status          # 查看分层
  arrow-lake lifecycle restore <key>   # 恢复 Glacier
  arrow-lake lifecycle estimate       # 成本估算
""")


if __name__ == "__main__":
    main()
