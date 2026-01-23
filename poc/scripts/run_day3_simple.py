#!/usr/bin/env python3
"""
Sprint 1 Week 2 Day 3 - LanceDB 并发搜索优化测试

重点:
1. 验证并发搜索性能提升
2. 测试不同并发级别的 QPS 改善
3. 对比单线程 vs 多线程性能
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

# 添加项目根目录到路径
project_root = Path(__file__).parent.parent.parent
sys.path.insert(0, str(project_root))

import numpy as np
import lancedb

# 测试配置
RESULTS_DIR = project_root / "poc" / "results"
os.makedirs(RESULTS_DIR, exist_ok=True)

# 使用临时目录
TEMP_DIR = tempfile.mkdtemp(prefix="lancedb_day3_")
print(f"使用临时目录: {TEMP_DIR}")


class LanceDBConcurrencyTest:
    """LanceDB 并发搜索性能测试"""

    def __init__(self, uri: str):
        self.uri = uri
        self.db = None
        self.table = None

    def setup(self, num_vectors: int = 50000, dimension: int = 128, table_name: str = None):
        """准备测试数据"""
        if table_name is None:
            table_name = f"test_{int(time.time())}"

        print(f"\n📊 准备测试数据: {num_vectors} 向量, {dimension} 维")
        print(f"   表名: {table_name}")

        try:
            self.db = lancedb.connect(self.uri)

            # 创建新表（每次使用不同的表名）
            print(f"   生成随机向量...")
            vectors = np.random.randn(num_vectors, dimension).astype(np.float32)
            categories = np.random.choice(["A", "B", "C"], num_vectors).tolist()

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
            import traceback
            traceback.print_exc()
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
            print(f"   启动 {num_threads} 个并发线程...")
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
    print("🚀 Sprint 1 Week 2 Day 3 - LanceDB 并发搜索优化测试")
    print("="*80)
    print(f"开始时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")

    start_time = time.time()
    all_results = []

    # 测试配置
    test_configs = [
        {"num_threads": 1, "name": "单线程"},
        {"num_threads": 5, "name": "5并发"},
        {"num_threads": 10, "name": "10并发"},
        {"num_threads": 20, "name": "20并发"},
    ]

    print("\n📋 测试计划:")
    for config in test_configs:
        print(f"  - {config['name']}: {config['num_threads']} 线程")

    # 运行测试
    for i, config in enumerate(test_configs, 1):
        print(f"\n{'='*80}")
        print(f"测试 {i}/{len(test_configs)}: {config['name']}")
        print('='*80)

        test = LanceDBConcurrencyTest(TEMP_DIR)

        # 准备数据（每个测试使用独立的表）
        if test.setup(num_vectors=50000, dimension=128):
            # 执行并发搜索测试
            perf_results = test.test_concurrent_search(
                num_queries=100,
                num_threads=config['num_threads'],
                k=10
            )

            if perf_results:
                all_results.append({
                    "test_name": config['name'],
                    "num_threads": config['num_threads'],
                    "success": True,
                    **perf_results
                })
            else:
                all_results.append({
                    "test_name": config['name'],
                    "num_threads": config['num_threads'],
                    "success": False
                })
        else:
            all_results.append({
                "test_name": config['name'],
                "num_threads": config['num_threads'],
                "success": False,
                "error": "Setup failed"
            })

    # 汇总结果
    end_time = time.time()
    total_tests = len(all_results)
    successful = sum(1 for r in all_results if r.get("success", False))
    failed = total_tests - successful

    print("\n" + "="*80)
    print("📊 Day 3 并发搜索测试汇总")
    print("="*80)
    print(f"总测试数: {total_tests}")
    print(f"✅ 成功: {successful}")
    print(f"❌ 失败: {failed}")
    print(f"成功率: {successful/total_tests*100:.1f}%" if total_tests > 0 else "N/A")
    print(f"总耗时: {end_time - start_time:.2f}s")

    # 性能对比
    print("\n📈 并发性能对比:")
    print(f"\n{'配置':<12} {'QPS':<12} {'P99延迟':<12} {'QPS提升':<12}")
    print("-" * 50)

    baseline_qps = None
    for result in all_results:
        if result.get("success"):
            name = result['test_name']
            qps = result.get('qps', 0)
            p99 = result.get('latency_p99_ms', 0)

            if baseline_qps is None:
                baseline_qps = qps
                improvement = "1.0x (基线)"
            else:
                improvement = f"{qps/baseline_qps:.1f}x"

            print(f"{name:<12} {qps:<12.2f} {p99:<12.2f} {improvement:<12}")

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
            "total_elapsed_s": end_time - start_time,
            "end_time": datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        }
    }

    results_file = RESULTS_DIR / f"day3_concurrency_{timestamp}.json"
    with open(results_file, 'w') as f:
        json.dump(summary, f, indent=2)

    print(f"\n✅ 结果已保存: {results_file}")

    # 结论
    print("\n🎯 关键发现:")
    if successful >= 3:
        best_result = max(all_results, key=lambda x: x.get('qps', 0))
        worst_result = min(all_results, key=lambda x: x.get('qps', 0))

        if best_result.get('qps', 0) > 0 and worst_result.get('qps', 0) > 0:
            improvement = best_result['qps'] / worst_result['qps']
            print(f"  ✅ 最佳配置: {best_result['test_name']} (QPS: {best_result['qps']:.2f})")
            print(f"  ✅ 性能提升: {improvement:.1f}x 相比单线程")

            if best_result['qps'] >= 1000:
                print(f"  ✅ 达成目标: QPS >= 1000")
            elif best_result['qps'] >= 500:
                print(f"  ⚠️  接近目标: QPS {best_result['qps']:.2f} (目标: 1000)")
            else:
                print(f"  ⚠️  未达目标: QPS {best_result['qps']:.2f} (目标: 1000)")

    return summary


if __name__ == "__main__":
    main()
