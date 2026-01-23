#!/usr/bin/env python3
"""
Sprint 1 Week 2 Day 3 - 高级 POC 验证测试

测试内容:
1. LanceDB 索引创建修复
2. LanceDB 并发搜索测试
3. MinIO S3 集成测试
4. 性能优化验证
"""

import os
import sys
import json
import time
import asyncio
import subprocess
from pathlib import Path
from datetime import datetime
from typing import Dict, List, Any, Optional
import concurrent.futures
import threading

# 添加项目根目录到路径
project_root = Path(__file__).parent.parent.parent
sys.path.insert(0, str(project_root))

import numpy as np
import lancedb
import daft as da
from dotenv import load_dotenv

# 加载环境变量
load_dotenv(project_root / ".env")

# 测试配置
RESULTS_DIR = project_root / "poc" / "results"
os.makedirs(RESULTS_DIR, exist_ok=True)

LANCEDB_URI = os.getenv("LANCEDB_URI", "/tmp/lancedb_poc_day3")
MINIO_ENDPOINT = os.getenv("MINIO_ENDPOINT", "localhost:9000")
MINIO_ACCESS_KEY = os.getenv("MINIO_ACCESS_KEY", "minioadmin")
MINIO_SECRET_KEY = os.getenv("MINIO_SECRET_KEY", "minioadmin123")


class LanceDBConcurrencyTest:
    """LanceDB 并发搜索测试"""

    def __init__(self, uri: str):
        self.uri = uri
        self.db = None
        self.table = None

    def setup(self, num_vectors: int = 50000, dimension: int = 128, table_name: str = "concurrency_test"):
        """准备测试数据"""
        print(f"\n📊 准备测试数据: {num_vectors} 向量, {dimension} 维")

        try:
            self.db = lancedb.connect(self.uri)

            # 删除已存在的表
            existing_tables = self.db.list_tables()
            if table_name in existing_tables:
                print(f"   删除已存在的表: {table_name}")
                self.db.drop_table(table_name)

            # 生成随机向量
            vectors = np.random.randn(num_vectors, dimension).astype(np.float32)
            categories = np.random.choice(["A", "B", "C"], num_vectors).tolist()

            # 创建表 - 使用列表格式
            print(f"   创建表并插入数据...")
            data = [
                {
                    "id": i,
                    "vector": vectors[i].tolist(),
                    "category": categories[i]
                }
                for i in range(num_vectors)
            ]

            self.table = self.db.create_table(table_name, data=data)

            print(f"✅ 数据准备完成: {num_vectors} 条记录")
            return True

        except Exception as e:
            print(f"❌ 数据准备失败: {e}")
            return False

    def single_search(self, query_vector: np.ndarray, k: int = 10) -> float:
        """单次搜索"""
        start = time.time()
        self.table.search(query_vector).limit(k).to_pandas()
        return time.time() - start

    def test_concurrent_search(
        self,
        num_queries: int = 100,
        num_threads: int = 10,
        k: int = 10
    ) -> Dict[str, Any]:
        """测试并发搜索性能"""
        print(f"\n🚀 测试并发搜索")
        print(f"   并发数: {num_threads}")
        print(f"   查询数: {num_queries}")
        print(f"   Top-K: {k}")

        try:
            # 生成查询向量
            dimension = 128
            query_vectors = [
                np.random.randn(dimension).astype(np.float32)
                for _ in range(num_queries)
            ]

            # 并发搜索
            latencies = []
            start_time = time.time()

            def worker(queries):
                """工作线程"""
                thread_times = []
                for query in queries:
                    query_start = time.time()
                    self.table.search(query).limit(k).to_pandas()
                    thread_times.append(time.time() - query_start)
                return thread_times

            # 分配查询到各线程
            queries_per_thread = num_queries // num_threads
            query_chunks = [
                query_vectors[i * queries_per_thread:(i + 1) * queries_per_thread]
                for i in range(num_threads)
            ]

            # 执行并发搜索
            with concurrent.futures.ThreadPoolExecutor(max_workers=num_threads) as executor:
                futures = [
                    executor.submit(worker, chunk)
                    for chunk in query_chunks
                ]

                for future in concurrent.futures.as_completed(futures):
                    latencies.extend(future.result())

            total_time = time.time() - start_time

            # 计算指标
            latencies_ms = [lat * 1000 for lat in latencies]
            latencies_array = np.array(latencies_ms)

            results = {
                "num_threads": num_threads,
                "total_queries": num_queries,
                "total_time_s": total_time,
                "qps": num_queries / total_time,
                "latency_avg_ms": float(np.mean(latencies_array)),
                "latency_p50_ms": float(np.percentile(latencies_array, 50)),
                "latency_p95_ms": float(np.percentile(latencies_array, 95)),
                "latency_p99_ms": float(np.percentile(latencies_array, 99)),
                "latency_min_ms": float(np.min(latencies_array)),
                "latency_max_ms": float(np.max(latencies_array)),
            }

            print(f"\n✅ 并发搜索完成")
            print(f"   总耗时: {total_time:.2f}s")
            print(f"   QPS: {results['qps']:.2f}")
            print(f"   P99 延迟: {results['latency_p99_ms']:.2f}ms")

            return results

        except Exception as e:
            print(f"❌ 并发搜索测试失败: {e}")
            import traceback
            traceback.print_exc()
            return None

    def test_index_creation(self) -> Dict[str, Any]:
        """测试索引创建 (修复版)"""
        print(f"\n🔧 测试索引创建")

        try:
            # 测试不同的索引类型 - 使用 LanceDB 支持的索引类型
            # LanceDB 支持: "ivf_pq", "hnsw", "auto" 等
            test_configs = [
                {"index_type": "ivf_pq", "name": "IVF_PQ"},
                {"index_type": "hnsw", "name": "HNSW"},
            ]

            results = {}

            for config in test_configs:
                index_type = config["index_type"]
                display_name = config["name"]
                print(f"\n   测试 {display_name} 索引...")

                start_time = time.time()

                try:
                    # 修复: 使用正确的索引类型
                    self.table.create_index(
                        "vector",
                        index_type=index_type
                    )

                    index_time = time.time() - start_time

                    results[display_name] = {
                        "success": True,
                        "time_s": index_time
                    }

                    print(f"   ✅ {display_name} 索引创建成功 ({index_time:.2f}s)")

                except Exception as e:
                    results[display_name] = {
                        "success": False,
                        "error": str(e)
                    }
                    print(f"   ❌ {display_name} 索引创建失败: {e}")

            return results

        except Exception as e:
            print(f"❌ 索引创建测试失败: {e}")
            return {}


class MinS3IntegrationTest:
    """MinIO S3 集成测试"""

    def __init__(self):
        self.endpoint = MINIO_ENDPOINT
        self.access_key = MINIO_ACCESS_KEY
        self.secret_key = MINIO_SECRET_KEY

    def test_s3_write(self) -> Dict[str, Any]:
        """测试 Daft 写入 S3"""
        print(f"\n📦 测试 MinIO S3 写入")

        try:
            # 创建测试数据
            print(f"   创建测试数据...")
            test_data = {
                "id": list(range(1000)),
                "value": np.random.randn(1000).tolist(),
                "category": np.random.choice(["A", "B", "C"], 1000).tolist()
            }

            df = da.from_pydict(test_data)

            # 测试写入 S3
            bucket_name = "dintellihub-processed"
            s3_path = f"s3://{bucket_name}/test_output.parquet"

            print(f"   配置 S3 环境...")
            # 配置 Daft S3
            os.environ["AWS_ENDPOINT_URL"] = f"http://{self.endpoint}"
            os.environ["AWS_ACCESS_KEY_ID"] = self.access_key
            os.environ["AWS_SECRET_ACCESS_KEY"] = self.secret_key
            os.environ["AWS_REGION"] = "us-east-1"
            os.environ["AWS_ALLOW_HTTP"] = "true"

            print(f"   写入 S3: {s3_path}")
            start_time = time.time()

            df.write_parquet(s3_path)

            write_time = time.time() - start_time

            print(f"✅ S3 写入成功 ({write_time:.2f}s)")

            return {
                "success": True,
                "write_time_s": write_time,
                "s3_path": s3_path
            }

            # 创建测试数据
            print(f"\n   创建测试数据...")
            test_data = {
                "id": list(range(1000)),
                "value": np.random.randn(1000).tolist(),
                "category": np.random.choice(["A", "B", "C"], 1000).tolist()
            }

            df = da.from_pydict(test_data)

            # 测试写入 S3
            s3_path = f"s3://{bucket_name}/test_output.parquet"

            print(f"   写入 S3: {s3_path}")
            start_time = time.time()

            # 配置 Daft S3
            os.environ["AWS_ENDPOINT_URL"] = f"http://{self.endpoint}"
            os.environ["AWS_ACCESS_KEY_ID"] = self.access_key
            os.environ["AWS_SECRET_ACCESS_KEY"] = self.secret_key

            df.write_parquet(s3_path)

            write_time = time.time() - start_time

            print(f"✅ S3 写入成功 ({write_time:.2f}s)")

            # 验证文件存在
            try:
                objects = s3_client.list_objects_v2(Bucket=bucket_name, Prefix="test_output/")
                if "Contents" in objects:
                    print(f"   ✅ 验证成功: 找到 {len(objects['Contents'])} 个对象")
                else:
                    print(f"   ⚠️  警告: 未找到文件")
            except Exception as e:
                print(f"   ⚠️  验证失败: {e}")

            return {
                "success": True,
                "write_time_s": write_time,
                "s3_path": s3_path
            }

        except Exception as e:
            print(f"❌ S3 写入测试失败: {e}")
            import traceback
            traceback.print_exc()
            return {"success": False, "error": str(e)}


def run_lancedb_tests() -> List[Dict]:
    """运行 LanceDB 测试"""
    print("\n" + "="*80)
    print("🧪 LanceDB 高级测试")
    print("="*80)

    results = []

    # 测试 1: 索引创建
    print("\n📋 测试 1: 索引创建修复")
    test = LanceDBConcurrencyTest(LANCEDB_URI)
    if test.setup(num_vectors=10000, dimension=128):
        index_results = test.test_index_creation()
        results.append({
            "component": "lancedb",
            "test": "index_creation",
            "success": any(r.get("success", False) for r in index_results.values()),
            "details": index_results
        })

    # 测试 2: 并发搜索 (不同并发级别)
    print("\n📋 测试 2: 并发搜索性能")
    for num_threads in [1, 5, 10, 20]:
        print(f"\n--- 并发级别: {num_threads} 线程 ---")
        test = LanceDBConcurrencyTest(LANCEDB_URI)
        if test.setup(num_vectors=50000, dimension=128):
            perf_results = test.test_concurrent_search(
                num_queries=100,
                num_threads=num_threads,
                k=10
            )

            if perf_results:
                results.append({
                    "component": "lancedb",
                    "test": f"concurrent_search_{num_threads}threads",
                    "success": True,
                    "elapsed": perf_results["total_time_s"],
                    "qps": perf_results["qps"],
                    "p99_latency_ms": perf_results["latency_p99_ms"],
                    "details": perf_results
                })

    return results


def run_minio_tests() -> List[Dict]:
    """运行 MinIO 测试"""
    print("\n" + "="*80)
    print("🧪 MinIO S3 集成测试")
    print("="*80)

    results = []

    test = MinS3IntegrationTest()
    s3_results = test.test_s3_write()

    results.append({
        "component": "minio",
        "test": "s3_write",
        "success": s3_results.get("success", False),
        "details": s3_results
    })

    return results


def main():
    """主测试流程"""
    print("\n" + "="*80)
    print("🚀 Sprint 1 Week 2 Day 3 - 高级 POC 验证")
    print("="*80)
    print(f"开始时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")

    start_time = time.time()
    all_results = []

    # 运行 LanceDB 测试
    try:
        lancedb_results = run_lancedb_tests()
        all_results.extend(lancedb_results)
    except Exception as e:
        print(f"❌ LanceDB 测试失败: {e}")

    # 运行 MinIO 测试
    try:
        minio_results = run_minio_tests()
        all_results.extend(minio_results)
    except Exception as e:
        print(f"❌ MinIO 测试失败: {e}")

    # 汇总结果
    end_time = time.time()
    total_tests = len(all_results)
    successful = sum(1 for r in all_results if r.get("success", False))
    failed = total_tests - successful

    print("\n" + "="*80)
    print("📊 Day 3 测试汇总")
    print("="*80)
    print(f"总测试数: {total_tests}")
    print(f"✅ 成功: {successful}")
    print(f"❌ 失败: {failed}")
    print(f"成功率: {successful/total_tests*100:.1f}%" if total_tests > 0 else "N/A")
    print(f"总耗时: {end_time - start_time:.2f}s")

    # 保存结果
    timestamp = int(time.time())
    summary = {
        "start_time": datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
        "tests": all_results,
        "summary": {
            "total_tests": total_tests,
            "successful": successful,
            "failed": failed,
            "success_rate": f"{successful/total_tests*100:.1f}%" if total_tests > 0 else "0%",
            "end_time": datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        }
    }

    results_file = RESULTS_DIR / f"day3_summary_{timestamp}.json"
    with open(results_file, 'w') as f:
        json.dump(summary, f, indent=2)

    print(f"\n✅ 结果已保存: {results_file}")

    # 详细结果
    print("\n📋 详细结果:")
    for result in all_results:
        status = "✅" if result.get("success", False) else "❌"
        test_name = f"{result['component']} - {result['test']}"
        print(f"  {status} {test_name}")
        if "qps" in result:
            print(f"     QPS: {result['qps']:.2f}")
            print(f"     P99: {result['p99_latency_ms']:.2f}ms")

    return summary


if __name__ == "__main__":
    main()
