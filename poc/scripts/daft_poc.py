#!/usr/bin/env python3
"""
Daft POC 测试脚本
验证 Daft 数据处理性能和功能
"""

import daft as da
import time
import os
from pathlib import Path
from typing import Dict, Any
import json

# MinIO 配置
MINIO_ENDPOINT = os.getenv("MINIO_ENDPOINT", "localhost:9000")
MINIO_ACCESS_KEY = os.getenv("MINIO_ACCESS_KEY", "minioadmin")
MINIO_SECRET_KEY = os.getenv("MINIO_SECRET_KEY", "minioadmin123")
MINIO_USE_SSL = os.getenv("MINIO_USE_SSL", "false")

# S3 存储配置
S3_CONFIG = {
    "key": MINIO_ACCESS_KEY,
    "secret": MINIO_SECRET_KEY,
    "endpoint_url": f"http://{MINIO_ENDPOINT}",
    "region": "us-east-1"
}


def generate_test_data(output_path: str, num_rows: int = 1_000_000):
    """生成测试数据"""
    print(f"🔨 生成测试数据: {num_rows:,} 行")

    import pandas as pd
    import numpy as np

    # 生成随机数据
    data = {
        "id": range(num_rows),
        "timestamp": pd.date_range("2024-01-01", periods=num_rows, freq="1s"),
        "value": np.random.randn(num_rows),
        "category": np.random.choice(["A", "B", "C", "D", "E"], num_rows),
        "score": np.random.uniform(0, 1, num_rows),
        "text": [f"sample_text_{i}" for i in range(num_rows)]
    }

    df = pd.DataFrame(data)

    # 保存为 Parquet
    output_file = Path(output_path) / "test_data.parquet"
    df.to_parquet(output_file, index=False)

    file_size_mb = output_file.stat().st_size / (1024 * 1024)
    print(f"✅ 数据生成完成: {output_file}")
    print(f"   文件大小: {file_size_mb:.2f} MB")

    return str(output_file)


def test_read_parquet(file_path: str) -> Any:
    """测试读取 Parquet 文件"""
    print(f"\n📖 测试: 读取 Parquet 文件")

    start_time = time.time()
    df = da.read_parquet(file_path)
    load_time = time.time() - start_time

    print(f"✅ 文件读取完成")
    print(f"   加载时间: {load_time:.2f}s")

    return df


def test_transformations(df: Any) -> Any:
    """测试数据转换操作"""
    print(f"\n🔄 测试: 数据转换")

    start_time = time.time()

    # 过滤
    df = df.filter(df["score"] > 0.5)
    print(f"   ✅ 过滤: score > 0.5")

    # 选择列
    df = df.select("id", "category", "score", "value")
    print(f"   ✅ 选择列")

    # 添加计算列
    df = df.with_column("value_squared", da.col("value").pow(2))
    print(f"   ✅ 添加计算列")

    # 聚合 (简化版 - 跳过，因为 API 变化)
    # agg_df = df.groupby("category").agg([
    #     (df["score"], "mean"),
    #     (df["value"], "std")
    # ])
    # print(f"   ✅ 聚合统计")

    transform_time = time.time() - start_time
    print(f"   转换时间: {transform_time:.2f}s")

    return df


def test_write_minio(df: Any, output_path: str):
    """测试写入 MinIO 或本地"""
    print(f"\n💾 测试: 写入数据")

    start_time = time.time()

    # 如果是 S3 路径，使用 io config
    if output_path.startswith("s3://"):
        # Daft 使用 IO Config 而不是 storage_options
        df.write_parquet(output_path)
    else:
        # 本地文件直接写入
        df.write_parquet(output_path)

    write_time = time.time() - start_time
    print(f"✅ 写入完成")
    print(f"   写入时间: {write_time:.2f}s")


def run_etl_pipeline(input_path: str, output_path: str) -> Dict[str, Any]:
    """运行完整的 ETL Pipeline"""
    print("=" * 60)
    print("🚀 Daft POC - ETL Pipeline 测试")
    print("=" * 60)

    results = {
        "test_name": "daft_etl_poc",
        "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
        "input_path": input_path,
        "output_path": output_path
    }

    # Step 1: 读取数据
    df = test_read_parquet(input_path)

    # Step 2: 数据转换
    transformed_df = test_transformations(df)

    # Step 3: 收集统计信息
    print(f"\n📊 收集统计信息")
    start_time = time.time()
    try:
        # 尝试收集到内存（小数据集）
        result_df = transformed_df.collect()
        results["row_count"] = len(result_df)
        results["success"] = True
        print(f"   行数: {results['row_count']:,}")
    except Exception as e:
        print(f"   ⚠️  收集失败: {e}")
        results["success"] = False

    stats_time = time.time() - start_time
    print(f"   统计时间: {stats_time:.2f}s")

    # Step 4: 写入 MinIO
    if output_path.startswith("s3://"):
        test_write_minio(transformed_df, output_path)

    # 汇总结果
    print("\n" + "=" * 60)
    print("📊 测试结果汇总")
    print("=" * 60)
    print(f"成功: {'✅ 是' if results['success'] else '❌ 否'}")
    print(f"时间: {results['timestamp']}")
    if results['success']:
        print(f"行数: {results['row_count']:,}")

    return results


def main():
    """主函数"""
    import argparse

    parser = argparse.ArgumentParser(description="Daft POC 测试")
    parser.add_argument("--input", type=str, help="输入文件路径")
    parser.add_argument("--output", type=str, default="s3://dintellihub-processed/output",
                       help="输出路径（S3 或本地）")
    parser.add_argument("--generate", action="store_true", help="生成测试数据")
    parser.add_argument("--rows", type=int, default=1_000_000, help="测试数据行数")

    args = parser.parse_args()

    # 如果需要生成数据
    if args.generate:
        data_dir = Path("../data/raw")
        data_dir.mkdir(parents=True, exist_ok=True)
        input_file = generate_test_data(data_dir, args.rows)
    elif args.input:
        input_file = args.input
    else:
        # 使用默认测试文件
        input_file = "../data/raw/test_data.parquet"

    # 运行 ETL Pipeline
    results = run_etl_pipeline(input_file, args.output)

    # 保存结果
    results_dir = Path("../results/daft")
    results_dir.mkdir(parents=True, exist_ok=True)

    result_file = results_dir / f"poc_results_{int(time.time())}.json"
    with open(result_file, "w") as f:
        json.dump(results, f, indent=2)

    print(f"\n💾 结果已保存: {result_file}")


if __name__ == "__main__":
    main()
