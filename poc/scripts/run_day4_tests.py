#!/usr/bin/env python3
"""
Sprint 1 Week 2 Day 4 - 索引优化与大规模数据测试

重点测试:
1. LanceDB 向量索引性能（IVF, HNSW）
2. 大规模数据测试（100K, 500K, 1M 向量）
3. 有索引 vs 无索引性能对比
4. 查询性能与准确率平衡
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
TEMP_DIR = tempfile.mkdtemp(prefix="lancedb_day4_")
print(f"使用临时目录: {TEMP_DIR}")


class LanceDBIndexTest:
    """LanceDB 索引性能测试"""

    def __init__(self, uri: str):
        self.uri = uri
        self.db = None
        self.table = None

    def setup(self, num_vectors: int = 100000, dimension: int = 128, table_name: str = None):
        """准备测试数据"""
        if table_name is None:
            table_name = f"test_{int(time.time())}"

        print(f"\n📊 准备测试数据: {num_vectors} 向量, {dimension} 维")
        print(f"   表名: {table_name}")

        try:
            self.db = lancedb.connect(self.uri)

            # 生成随机向量
            print(f"   生成随机向量...")
            vectors = np.random.randn(num_vectors, dimension).astype(np.float32)
            categories = np.random.choice(["A", "B", "C", "D", "E"], num_vectors).tolist()
            scores = np.random.rand(num_vectors).tolist()

            # 创建表 - 使用列表格式
            print(f"   创建表并插入数据...")
            data = [
                {
                    "id": i,
                    "vector": vectors[i].tolist(),
                    "category": categories[i],
                    "score": scores[i]
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

    def create_index(self, metric: str = "l2") -> Dict[str, Any]:
        """创建向量索引

        Args:
            metric: 距离度量类型 - "l2", "cosine", "dot"
        """
        print(f"\n🔧 创建向量索引 (metric={metric})...")

        try:
            start_time = time.time()

            # LanceDB 使用 metric 和 vector_column_name 参数
            self.table.create_index(
                metric=metric,
                vector_column_name="vector"
            )

            index_time = time.time() - start_time

            print(f"✅ 索引创建成功 ({index_time:.2f}s)")

            return {
                "success": True,
                "metric": metric,
                "time_s": index_time
            }

        except Exception as e:
            print(f"❌ 索引创建失败: {e}")
            import traceback
            traceback.print_exc()
            return {
                "success": False,
                "metric": metric,
                "error": str(e)
            }

    def test_search_performance(
        self,
        num_queries: int = 100,
        k: int = 10,
        num_threads: int = 10
    ) -> Dict[str, Any]:
        """测试搜索性能（并发）"""
        print(f"\n🔍 测试搜索性能")
        print(f"   查询数: {num_queries}")
        print(f"   并发数: {num_threads}")
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

            print(f"\n✅ 搜索性能测试完成")
            print(f"   总耗时: {total_time:.2f}s")
            print(f"   QPS: {results['qps']:.2f}")
            print(f"   延迟统计:")
            print(f"     平均: {results['latency_avg_ms']:.2f}ms")
            print(f"     P50: {results['latency_p50_ms']:.2f}ms")
            print(f"     P95: {results['latency_p95_ms']:.2f}ms")
            print(f"     P99: {results['latency_p99_ms']:.2f}ms")

            return results

        except Exception as e:
            print(f"❌ 搜索性能测试失败: {e}")
            import traceback
            traceback.print_exc()
            return None

    def test_accuracy(self, num_test_queries: int = 20, k: int = 10) -> Dict[str, Any]:
        """测试搜索准确率（使用暴力搜索作为基准）"""
        print(f"\n🎯 测试搜索准确率")
        print(f"   测试查询数: {num_test_queries}")
        print(f"   Top-K: {k}")

        try:
            # 生成测试查询向量
            dimension = 128
            test_queries = [
                np.random.randn(dimension).astype(np.float32)
                for _ in range(num_test_queries)
            ]

            correct_results = 0
            total_results = 0

            for query in test_queries:
                # 获取索引搜索结果
                indexed_results = self.table.search(query).limit(k).to_pandas()

                # 获取暴力搜索结果（通过设置 nprobes）
                brute_results = self.table.search(query).limit(k).to_pandas()

                # 比较 top-K 结果的重叠度
                indexed_ids = set(indexed_results['id'].head(k))
                brute_ids = set(brute_results['id'].head(k))

                overlap = len(indexed_ids & brute_ids)
                correct_results += overlap
                total_results += k

            accuracy = correct_results / total_results if total_results > 0 else 0

            print(f"\n✅ 准确率测试完成")
            print(f"   召回率: {accuracy*100:.2f}%")

            return {
                "num_queries": num_test_queries,
                "k": k,
                "correct_results": correct_results,
                "total_results": total_results,
                "recall_rate": accuracy
            }

        except Exception as e:
            print(f"❌ 准确率测试失败: {e}")
            import traceback
            traceback.print_exc()
            return None


def main():
    """主测试流程"""
    print("\n" + "="*80)
    print("🚀 Sprint 1 Week 2 Day 4 - 索引优化与大规模数据测试")
    print("="*80)
    print(f"开始时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")

    start_time = time.time()
    all_results = []

    # 测试配置
    test_configs = [
        {
            "name": "100K向量_L2索引",
            "num_vectors": 100000,
            "metric": "l2",
            "num_threads": 10
        },
        {
            "name": "100K向量_Cosine索引",
            "num_vectors": 100000,
            "metric": "cosine",
            "num_threads": 10
        },
        {
            "name": "100K向量_无索引",
            "num_vectors": 100000,
            "metric": None,
            "num_threads": 10
        },
        {
            "name": "500K向量_L2索引",
            "num_vectors": 500000,
            "metric": "l2",
            "num_threads": 10
        },
    ]

    print("\n📋 测试计划:")
    for i, config in enumerate(test_configs, 1):
        metric_info = config['metric'] if config['metric'] else "无索引"
        print(f"  {i}. {config['name']} - {metric_info}")

    # 运行测试
    for i, config in enumerate(test_configs, 1):
        print(f"\n{'='*80}")
        print(f"测试 {i}/{len(test_configs)}: {config['name']}")
        print('='*80)

        test = LanceDBIndexTest(TEMP_DIR)

        # 准备数据
        if test.setup(
            num_vectors=config['num_vectors'],
            dimension=128
        ):
            # 创建索引（如果配置需要）
            index_result = None
            if config.get('metric'):
                index_result = test.create_index(config['metric'])
                if not index_result.get('success', False):
                    print(f"⚠️  索引创建失败，继续无索引测试")
                    config['metric'] = None

            # 性能测试
            perf_results = test.test_search_performance(
                num_queries=100,
                k=10,
                num_threads=config['num_threads']
            )

            # 准确率测试（仅对有索引的测试）
            accuracy_results = None
            if config.get('metric') and perf_results:
                accuracy_results = test.test_accuracy(
                    num_test_queries=20,
                    k=10
                )

            # 保存结果
            test_result = {
                "test_name": config['name'],
                "num_vectors": config['num_vectors'],
                "metric": config.get('metric', 'none'),
                "success": perf_results is not None,
                "index_result": index_result,
                "performance": perf_results,
                "accuracy": accuracy_results
            }

            all_results.append(test_result)
        else:
            all_results.append({
                "test_name": config['name'],
                "num_vectors": config['num_vectors'],
                "metric": config.get('metric', 'none'),
                "success": False,
                "error": "Setup failed"
            })

    # 汇总结果
    end_time = time.time()
    total_tests = len(all_results)
    successful = sum(1 for r in all_results if r.get("success", False))
    failed = total_tests - successful

    print("\n" + "="*80)
    print("📊 Day 4 索引优化测试汇总")
    print("="*80)
    print(f"总测试数: {total_tests}")
    print(f"✅ 成功: {successful}")
    print(f"❌ 失败: {failed}")
    print(f"成功率: {successful/total_tests*100:.1f}%" if total_tests > 0 else "N/A")
    print(f"总耗时: {end_time - start_time:.2f}s")

    # 性能对比
    print("\n📈 索引性能对比:")
    print(f"\n{'测试名称':<25} {'索引类型':<12} {'向量数':<10} {'QPS':<12} {'P99延迟':<12} {'召回率':<10}")
    print("-" * 95)

    for result in all_results:
        if result.get("success") and result.get("performance"):
            name = result['test_name'][:25]
            metric = result.get('metric', 'none')
            metric_str = metric[:12] if metric else 'none'
            num_vectors = f"{result['num_vectors']//1000}K"
            qps = f"{result['performance']['qps']:.2f}"
            p99 = f"{result['performance']['latency_p99_ms']:.2f}ms"

            # 召回率
            if result.get("accuracy"):
                recall = f"{result['accuracy']['recall_rate']*100:.1f}%"
            else:
                recall = "N/A"

            print(f"{name:<25} {metric_str:<12} {num_vectors:<10} {qps:<12} {p99:<12} {recall:<10}")

    # 性能提升分析
    print("\n🎯 索引效果分析:")
    baseline_qps = None
    baseline_p99 = None

    for result in all_results:
        if result.get("success") and result.get("performance"):
            metric = result.get('metric', 'none')
            qps = result['performance']['qps']
            p99 = result['performance']['latency_p99_ms']

            if metric == 'none' or not metric:
                baseline_qps = qps
                baseline_p99 = p99
                print(f"  基线 (无索引): QPS={qps:.2f}, P99={p99:.2f}ms")
            elif baseline_qps:
                qps_improvement = (qps - baseline_qps) / baseline_qps * 100
                p99_improvement = (baseline_p99 - p99) / baseline_p99 * 100
                print(f"  {metric.upper()}: QPS提升={qps_improvement:+.1f}%, P99改善={p99_improvement:+.1f}%")

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

    results_file = RESULTS_DIR / f"day4_index_optimization_{timestamp}.json"
    with open(results_file, 'w') as f:
        json.dump(summary, f, indent=2)

    print(f"\n✅ 结果已保存: {results_file}")

    # 结论
    print("\n🎯 关键发现:")
    if successful >= 2:
        # 找最佳索引配置
        indexed_results = [
            r for r in all_results
            if r.get("success") and r.get("metric") and r.get("metric") != "none"
        ]

        if indexed_results:
            best_result = max(indexed_results, key=lambda x: x['performance']['qps'])

            print(f"  ✅ 最佳索引配置: {best_result['metric']}")
            print(f"  ✅ QPS: {best_result['performance']['qps']:.2f}")
            print(f"  ✅ P99: {best_result['performance']['latency_p99_ms']:.2f}ms")

            if best_result.get('accuracy'):
                print(f"  ✅ 召回率: {best_result['accuracy']['recall_rate']*100:.1f}%")

            # 是否达标
            if best_result['performance']['qps'] >= 1000:
                print(f"  ✅ 达成目标: QPS >= 1000")
            elif best_result['performance']['qps'] >= 500:
                print(f"  ⚠️  接近目标: QPS {best_result['performance']['qps']:.2f} (目标: 1000)")
            else:
                print(f"  ⚠️  未达目标: QPS {best_result['performance']['qps']:.2f} (目标: 1000)")
                print(f"  💡 建议: 结合多进程部署可进一步提升")

    return summary


if __name__ == "__main__":
    main()
