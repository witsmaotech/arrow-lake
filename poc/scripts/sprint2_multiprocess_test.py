#!/usr/bin/env python3
"""
Sprint 2 - LanceDB 多进程性能测试

目标：验证多进程部署效果，实现 QPS > 1000
"""

import os
import sys
import json
import time
import tempfile
from pathlib import Path
from datetime import datetime
from typing import Dict, List, Any
import concurrent.futures
import multiprocessing as mp

# 添加项目根目录到路径
project_root = Path(__file__).parent.parent.parent
sys.path.insert(0, str(project_root))

import numpy as np
import requests

# 测试配置
RESULTS_DIR = project_root / "poc" / "results"
os.makedirs(RESULTS_DIR, exist_ok=True)

LANCEDB_URL = "http://localhost:8765"


class LanceDBMultiProcessTest:
    """LanceDB 多进程性能测试"""

    def __init__(self, base_url: str):
        self.base_url = base_url
        self.collection_name = f"sprint2_test_{int(time.time())}"

    def check_service_health(self) -> bool:
        """检查服务健康状态"""
        try:
            response = requests.get(f"{self.base_url}/health", timeout=5)
            return response.status_code == 200
        except Exception as e:
            print(f"❌ 服务健康检查失败: {e}")
            return False

    def setup_test_data(self, num_vectors: int = 10000):
        """创建测试数据"""
        print(f"\n📊 创建测试数据: {num_vectors} 向量")

        try:
            # 生成测试数据
            vectors = np.random.randn(num_vectors, 128).astype(np.float32)
            categories = np.random.choice(["A", "B", "C"], num_vectors)
            scores = np.random.rand(num_vectors)

            # 准备 upsert 数据
            items = []
            for i in range(num_vectors):
                items.append({
                    "id": f"vec_{i}",
                    "vector": vectors[i].tolist(),
                    "category": categories[i],
                    "score": float(scores[i])
                })

            # 批量 upsert (每批 1000 条)
            batch_size = 1000
            total_batches = (num_vectors + batch_size - 1) // batch_size

            for batch_idx in range(total_batches):
                start_idx = batch_idx * batch_size
                end_idx = min(start_idx + batch_size, num_vectors)
                batch_items = items[start_idx:end_idx]

                # 重新构造 batch_items，确保 metadata 字段格式正确
                formatted_items = []
                for item in batch_items:
                    formatted_items.append({
                        "id": item["id"],
                        "vector": item["vector"],
                        "metadata": {
                            "category": item["category"],
                            "score": item["score"]
                        }
                    })

                response = requests.post(
                    f"{self.base_url}/api/v1/upsert",
                    json={
                        "collection": self.collection_name,
                        "items": formatted_items,
                        "mode": "overwrite"
                    },
                    timeout=60
                )

                if response.status_code != 200:
                    print(f"❌ Upsert 失败 (batch {batch_idx}): {response.text}")
                    return False

                print(f"   批次 {batch_idx + 1}/{total_batches} 完成")

            print(f"✅ 测试数据创建成功: {num_vectors} 条记录")
            return True

        except Exception as e:
            print(f"❌ 创建测试数据失败: {e}")
            import traceback
            traceback.print_exc()
            return False

    def test_concurrent_search(
        self,
        num_queries: int = 200,
        num_threads: int = 20,
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
                np.random.randn(dimension).astype(np.float32).tolist()
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
                    try:
                        response = requests.post(
                            f"{self.base_url}/api/v1/search",
                            json={
                                "collection": self.collection_name,
                                "vector": query,
                                "limit": k
                            },
                            timeout=30
                        )
                        if response.status_code == 200:
                            thread_times.append(time.time() - query_start)
                    except Exception as e:
                        print(f"⚠️  查询失败: {e}")
                return thread_times

            # 分配查询到各线程
            queries_per_thread = num_queries // num_threads
            query_chunks = [
                query_vectors[i * queries_per_thread:(i + 1) * queries_per_thread]
                for i in range(num_threads)
            ]

            # 执行并发搜索
            print(f"   启动 {num_threads} 个并发线程...")
            with concurrent.futures.ThreadPoolExecutor(max_workers=num_threads) as executor:
                futures = [
                    executor.submit(worker, chunk)
                    for chunk in query_chunks
                ]

                for future in concurrent.futures.as_completed(futures):
                    latencies.extend(future.result())

            total_time = time.time() - start_time

            if not latencies:
                print(f"❌ 所有查询失败")
                return None

            # 计算指标
            latencies_ms = [lat * 1000 for lat in latencies]
            latencies_array = np.array(latencies_ms)

            results = {
                "num_threads": num_threads,
                "total_queries": num_queries,
                "successful_queries": len(latencies),
                "total_time_s": total_time,
                "qps": len(latencies) / total_time,
                "latency_avg_ms": float(np.mean(latencies_array)),
                "latency_p50_ms": float(np.percentile(latencies_array, 50)),
                "latency_p95_ms": float(np.percentile(latencies_array, 95)),
                "latency_p99_ms": float(np.percentile(latencies_array, 99)),
                "latency_min_ms": float(np.min(latencies_array)),
                "latency_max_ms": float(np.max(latencies_array)),
            }

            print(f"\n✅ 并发搜索完成")
            print(f"   成功查询: {len(latencies)}/{num_queries}")
            print(f"   总耗时: {total_time:.2f}s")
            print(f"   QPS: {results['qps']:.2f}")
            print(f"   延迟统计:")
            print(f"     平均: {results['latency_avg_ms']:.2f}ms")
            print(f"     P50: {results['latency_p50_ms']:.2f}ms")
            print(f"     P95: {results['latency_p95_ms']:.2f}ms")
            print(f"     P99: {results['latency_p99_ms']:.2f}ms")

            return results

        except Exception as e:
            print(f"❌ 并发搜索测试失败: {e}")
            import traceback
            traceback.print_exc()
            return None


def main():
    """主测试流程"""
    print("\n" + "="*80)
    print("🚀 Sprint 2 - LanceDB 多进程性能测试")
    print("="*80)
    print(f"开始时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")

    # 检查服务健康
    test = LanceDBMultiProcessTest(LANCEDB_URL)
    if not test.check_service_health():
        print("\n❌ LanceDB 服务未运行，请先启动服务")
        print("   启动命令: docker compose up -d lancedb-service")
        return

    print("\n✅ LanceDB 服务健康")

    # 创建测试数据
    if not test.setup_test_data(num_vectors=10000):
        print("\n❌ 测试数据创建失败，无法继续")
        return

    # 测试不同并发级别
    test_configs = [
        {"num_threads": 1, "num_queries": 100},
        {"num_threads": 10, "num_queries": 100},
        {"num_threads": 20, "num_queries": 100},
        {"num_threads": 30, "num_queries": 100},
        {"num_threads": 50, "num_queries": 100},
    ]

    print("\n📋 测试计划:")
    for i, config in enumerate(test_configs, 1):
        print(f"  {i}. {config['num_threads']} 并发线程 - {config['num_queries']} 查询")

    all_results = []
    start_time = time.time()

    for i, config in enumerate(test_configs, 1):
        print(f"\n{'='*80}")
        print(f"测试 {i}/{len(test_configs)}: {config['num_threads']} 并发线程")
        print('='*80)

        result = test.test_concurrent_search(
            num_queries=config['num_queries'],
            num_threads=config['num_threads'],
            k=10
        )

        if result:
            all_results.append(result)

    total_time = time.time() - start_time

    # 汇总结果
    print("\n" + "="*80)
    print("📊 多进程性能测试汇总")
    print("="*80)
    print(f"总测试数: {len(all_results)}")
    print(f"总耗时: {total_time:.2f}s")

    # 性能对比
    print("\n📈 并发性能对比:")
    print(f"\n{'并发线程':<12} {'QPS':<12} {'P99延迟':<12} {'成功率':<10}")
    print("-" * 50)

    for result in all_results:
        threads = result['num_threads']
        qps = f"{result['qps']:.2f}"
        p99 = f"{result['latency_p99_ms']:.2f}ms"
        success_rate = f"{result['successful_queries']/result['total_queries']*100:.1f}%"
        print(f"{threads:<12} {qps:<12} {p99:<12} {success_rate:<10}")

    # 找到最佳配置
    if all_results:
        best_result = max(all_results, key=lambda x: x['qps'])

        print("\n🎯 关键发现:")
        print(f"  ✅ 最佳配置: {best_result['num_threads']} 并发线程")
        print(f"  ✅ 最佳 QPS: {best_result['qps']:.2f}")

        if best_result['qps'] >= 1000:
            print(f"  ✅ 达成目标: QPS >= 1000")
        elif best_result['qps'] >= 500:
            print(f"  ⚠️  接近目标: QPS {best_result['qps']:.2f} (目标: 1000)")
        else:
            print(f"  ❌ 未达目标: QPS {best_result['qps']:.2f} (目标: 1000)")

        # 与 Sprint 1 对比
        sprint1_qps = 283.21
        improvement = (best_result['qps'] - sprint1_qps) / sprint1_qps * 100
        print(f"\n📊 与 Sprint 1 对比:")
        print(f"  Sprint 1 (单进程): {sprint1_qps:.2f} QPS")
        print(f"  Sprint 2 (多进程): {best_result['qps']:.2f} QPS")
        print(f"  提升: {improvement:+.1f}%")

    # 保存结果
    timestamp = int(time.time())
    summary = {
        "start_time": datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
        "tests": all_results,
        "sprint1_baseline": {
            "qps": 283.21,
            "p99_ms": 41.60
        },
        "end_time": datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    }

    results_file = RESULTS_DIR / f"sprint2_multiprocess_{timestamp}.json"
    with open(results_file, 'w') as f:
        json.dump(summary, f, indent=2)

    print(f"\n✅ 结果已保存: {results_file}")


if __name__ == "__main__":
    main()
