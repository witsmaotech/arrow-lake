#!/usr/bin/env python3
"""
Sprint 2 - LanceDB 多进程性能测试 (直接使用 LanceDB Python 库)

目标：绕过 GIL 限制，使用 multiprocessing 实现 QPS > 1000
"""

import os
import sys
import json
import time
import tempfile
from pathlib import Path
from datetime import datetime
from typing import Dict, List, Any
import multiprocessing as mp
import numpy as np

# 添加项目根目录到路径
project_root = Path(__file__).parent.parent.parent
sys.path.insert(0, str(project_root))

import lancedb

# 测试配置
RESULTS_DIR = project_root / "poc" / "results"
os.makedirs(RESULTS_DIR, exist_ok=True)

# 使用临时目录
TEMP_DIR = tempfile.mkdtemp(prefix="lancedb_sprint2_")


def worker_search_task(args):
    """Worker 任务：处理单个搜索请求"""
    query_id, query_vector, db_uri, table_name, k = args

    try:
        # 每个进程有自己的 LanceDB 连接
        db = lancedb.connect(db_uri)
        table = db.open_table(table_name)

        start = time.time()

        # 执行搜索
        results_df = table.search(query_vector).limit(k).to_pandas()

        latency = time.time() - start
        return (query_id, latency)

    except Exception as e:
        print(f"Worker error: {e}")
        return None


class LanceDBMultiprocessTester:
    """LanceDB 多进程性能测试器"""

    def __init__(self, uri: str):
        self.uri = uri
        self.db = None
        self.table = None
        self.table_name = None

    def setup(self, num_vectors: int = 50000, dimension: int = 128):
        """准备测试数据"""
        self.table_name = f"test_{int(time.time())}"

        print(f"\n📊 准备测试数据: {num_vectors} 向量, {dimension} 维")
        print(f"   表名: {self.table_name}")

        try:
            self.db = lancedb.connect(self.uri)

            # 生成测试数据
            print(f"   生成随机向量...")
            vectors = np.random.randn(num_vectors, dimension).astype(np.float32)
            categories = np.random.choice(["A", "B", "C"], num_vectors)
            scores = np.random.rand(num_vectors)

            # 创建表（使用列表格式）
            print(f"   创建表并插入数据...")
            data = [
                {
                    "id": i,
                    "vector": vectors[i].tolist(),
                    "category": categories[i],
                    "score": float(scores[i])
                }
                for i in range(num_vectors)
            ]

            self.table = self.db.create_table(self.table_name, data=data)

            print(f"✅ 数据准备完成: {num_vectors} 条记录")
            return True

        except Exception as e:
            print(f"❌ 数据准备失败: {e}")
            import traceback
            traceback.print_exc()
            return False

    def test_multiprocess_search(
        self,
        num_queries: int = 100,
        num_workers: int = 4,
        k: int = 10
    ) -> Dict[str, Any]:
        """测试多进程搜索性能"""
        print(f"\n🚀 测试多进程搜索")
        print(f"   Worker 数: {num_workers}")
        print(f"   查询数: {num_queries}")
        print(f"   Top-K: {k}")

        try:
            # 生成查询向量
            dimension = 128
            query_vectors = [
                np.random.randn(dimension).astype(np.float32)
                for _ in range(num_queries)
            ]

            # 准备任务参数
            tasks = [
                (i, query.tolist(), self.uri, self.table_name, k)
                for i, query in enumerate(query_vectors)
            ]

            # 使用 spawn context 启动进程池
            start_time = time.time()

            with mp.get_context('spawn').Pool(processes=num_workers) as pool:
                results = pool.map(worker_search_task, tasks)

            total_time = time.time() - start_time

            # 过滤成功的结果
            successful_results = [r for r in results if r is not None]
            success_count = len(successful_results)

            print(f"\n✅ 多进程搜索完成")
            print(f"   成功查询: {success_count}/{num_queries}")
            print(f"   总耗时: {total_time:.2f}s")
            print(f"   QPS: {success_count / total_time:.2f}")

            return {
                "num_workers": num_workers,
                "num_queries": num_queries,
                "successful_queries": success_count,
                "total_time_s": total_time,
                "qps": success_count / total_time
            }

        except Exception as e:
            print(f"❌ 多进程搜索测试失败: {e}")
            import traceback
            traceback.print_exc()
            return None


def main():
    """主测试流程"""
    print("\n" + "="*80)
    print("🚀 Sprint 2 - LanceDB 多进程性能测试 (直接使用 LanceDB 库)")
    print("="*80)
    print(f"开始时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")

    start_time = time.time()
    all_results = []

    # 测试配置：不同的 worker 数量
    test_configs = [
        {"num_workers": 1, "num_queries": 100},
        {"num_workers": 2, "num_queries": 100},
        {"num_workers": 4, "num_queries": 100},
        {"num_workers": 8, "num_queries": 100},
    ]

    print("\n📋 测试计划:")
    for i, config in enumerate(test_configs, 1):
        print(f"  {i}. {config['num_workers']} Worker 进程 - {config['num_queries']} 查询")

    # 运行测试
    for i, config in enumerate(test_configs, 1):
        print(f"\n{'='*80}")
        print(f"测试 {i}/{len(test_configs)}: {config['num_workers']} Worker 进程")
        print('='*80)

        tester = LanceDBMultiprocessTester(TEMP_DIR)

        # 准备数据（每次测试使用独立的表）
        if tester.setup(num_vectors=50000, dimension=128):
            # 执行多进程测试
            result = tester.test_multiprocess_search(
                num_queries=config['num_queries'],
                num_workers=config['num_workers'],
                k=10
            )

            if result:
                all_results.append(result)
        else:
            print(f"❌ 测试失败")

    # 汇总结果
    end_time = time.time()
    total_time = end_time - start_time

    print("\n" + "="*80)
    print("📊 Sprint 2 多进程性能测试汇总")
    print("="*80)
    print(f"总测试数: {len(all_results)}")
    print(f"总耗时: {total_time:.2f}s")

    # 性能对比
    if all_results:
        print("\n📈 多进程性能对比:")
        print(f"\n{'Worker 数':<12} {'QPS':<12} {'提升倍数':<12}")
        print("-" * 40)

        baseline_qps = None
        for result in all_results:
            workers = result['num_workers']
            qps = result['qps']

            if baseline_qps is None:
                baseline_qps = qps
                print(f"{workers:<12} {qps:<12.2f} 1.0x (基线)")
            else:
                improvement = qps / baseline_qps
                print(f"{workers:<12} {qps:<12.2f} {improvement:.2f}x")

        # 找最佳配置
        best_result = max(all_results, key=lambda x: x['qps'])

        print(f"\n🎯 关键发现:")
        print(f"  ✅ 最佳配置: {best_result['num_workers']} Worker 进程")
        print(f"  ✅ 最佳 QPS: {best_result['qps']:.2f}")

        sprint1_qps = 283.21
        improvement = (best_result['qps'] - sprint1_qps) / sprint1_qps * 100

        print(f"\n📊 与 Sprint 1 对比:")
        print(f"  Sprint 1 (单进程): {sprint1_qps:.2f} QPS")
        print(f"  Sprint 2 (多进程): {best_result['qps']:.2f} QPS")
        print(f"  提升: {improvement:+.1f}%")

        if best_result['qps'] >= 1000:
            print(f"\n  ✅ 达成目标: QPS >= 1000")
        elif best_result['qps'] >= 500:
            print(f"\n  ⚠️  接近目标: QPS {best_result['qps']:.2f} (目标: 1000)")
        else:
            print(f"\n  ❌ 未达目标: QPS {best_result['qps']:.2f} (目标: 1000)")
            print(f"  💡 建议: 增加更多 worker 或使用分布式部署")

    # 保存结果
    timestamp = int(time.time())
    summary = {
        "start_time": datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
        "tests": all_results,
        "sprint1_baseline": {
            "qps": 283.21,
            "p99_ms": 41.60
        },
        "total_elapsed_s": total_time,
        "end_time": datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    }

    results_file = RESULTS_DIR / f"sprint2_multiprocess_{timestamp}.json"
    with open(results_file, 'w') as f:
        json.dump(summary, f, indent=2)

    print(f"\n✅ 结果已保存: {results_file}")


if __name__ == "__main__":
    main()
