#!/usr/bin/env python3
"""
DIntelliHub POC Day 2 - 完整验证测试脚本
自动化执行所有 Daft 和 LanceDB POC 测试
"""

import subprocess
import time
import json
from pathlib import Path
from datetime import datetime

# 测试配置
TESTS = {
    "lancedb": [
        {"count": 10000, "dimension": 128, "name": "10K_128d"},
        {"count": 50000, "dimension": 128, "name": "50K_128d"},
        {"count": 100000, "dimension": 128, "name": "100K_128d"},
    ],
    "daft": [
        {"rows": 10000, "name": "10K_rows"},
        {"rows": 100000, "name": "100K_rows"},
    ]
}

results = {
    "start_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    "tests": [],
    "summary": {}
}


def run_lancedb_tests():
    """运行 LanceDB POC 测试"""
    print("\n" + "="*80)
    print("🎯 LanceDB POC - Day 2 完整验证")
    print("="*80)

    for test in TESTS["lancedb"]:
        print(f"\n📊 测试: {test['name']}")
        print(f"   向量数: {test['count']:,}")
        print(f"   维度: {test['dimension']}")

        cmd = f"python3 poc/scripts/lancedb_poc.py --count {test['count']} --dimension {test['dimension']}"

        start_time = time.time()
        try:
            subprocess.run(cmd, shell=True, check=True, timeout=300)
            elapsed = time.time() - start_time
            print(f"✅ 完成 (耗时: {elapsed:.1f}s)")

            results["tests"].append({
                "component": "lancedb",
                "test": test['name'],
                "success": True,
                "elapsed": elapsed
            })
        except Exception as e:
            elapsed = time.time() - start_time
            print(f"❌ 失败: {e}")
            results["tests"].append({
                "component": "lancedb",
                "test": test['name'],
                "success": False,
                "elapsed": elapsed
            })


def run_daft_tests():
    """运行 Daft POC 测试"""
    print("\n" + "="*80)
    print("🚀 Daft POC - Day 2 完整验证")
    print("="*80)

    # 先生成测试数据
    print("\n📊 生成测试数据...")
    for test in TESTS["daft"]:
        print(f"\n   生成 {test['name']}...")
        cmd = f"python3 poc/scripts/daft_poc.py --generate --rows {test['rows']}"

        start_time = time.time()
        try:
            subprocess.run(cmd, shell=True, check=True, timeout=120)
            elapsed = time.time() - start_time
            print(f"✅ 完成 (耗时: {elapsed:.1f}s)")

            results["tests"].append({
                "component": "daft_generate",
                "test": test['name'],
                "success": True,
                "elapsed": elapsed
            })
        except Exception as e:
            elapsed = time.time() - start_time
            print(f"❌ 失败: {e}")
            results["tests"].append({
                "component": "daft_generate",
                "test": test['name'],
                "success": False,
                "elapsed": elapsed
            })

    # 运行 ETL 测试
    print("\n📊 运行 ETL Pipeline 测试...")
    for test in TESTS["daft"]:
        print(f"\n   测试 {test['name']}...")
        cmd = f"python3 poc/scripts/daft_poc.py --input ../data/raw/test_data.parquet --output /tmp/daft_output_{test['name']}"

        start_time = time.time()
        try:
            subprocess.run(cmd, shell=True, check=True, timeout=120)
            elapsed = time.time() - start_time
            print(f"✅ 完成 (耗时: {elapsed:.1f}s)")

            results["tests"].append({
                "component": "daft_etl",
                "test": test['name'],
                "success": True,
                "elapsed": elapsed
            })
        except Exception as e:
            elapsed = time.time() - start_time
            print(f"❌ 失败: {e}")
            results["tests"].append({
                "component": "daft_etl",
                "test": test['name'],
                "success": False,
                "elapsed": elapsed
            })


def generate_summary():
    """生成测试总结"""
    print("\n" + "="*80)
    print("📊 Day 2 测试总结")
    print("="*80)

    total_tests = len(results["tests"])
    successful = sum(1 for t in results["tests"] if t["success"])
    failed = total_tests - successful

    results["summary"] = {
        "total_tests": total_tests,
        "successful": successful,
        "failed": failed,
        "success_rate": f"{(successful/total_tests)*100:.1f}%",
        "end_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    }

    print(f"\n✅ 成功: {successful}/{total_tests} ({results['summary']['success_rate']})")
    print(f"❌ 失败: {failed}/{total_tests}")

    # 按组件统计
    print(f"\n📊 组件测试统计:")
    components = {}
    for test in results["tests"]:
        comp = test["component"]
        if comp not in components:
            components[comp] = {"total": 0, "success": 0}
        components[comp]["total"] += 1
        if test["success"]:
            components[comp]["success"] += 1

    for comp, stats in components.items():
        rate = f"{(stats['success']/stats['total'])*100:.1f}%"
        print(f"   {comp}: {stats['success']}/{stats['total']} ({rate})")

    # 保存结果
    results_dir = Path("poc/results")
    results_dir.mkdir(parents=True, exist_ok=True)

    result_file = results_dir / f"day2_summary_{int(time.time())}.json"
    with open(result_file, "w") as f:
        json.dump(results, f, indent=2, default=str)

    print(f"\n💾 结果已保存: {result_file}")

    return results


def main():
    """主函数"""
    print("="*80)
    print("🎯 DIntelliHub - Week 2 Day 2 完整 POC 验证")
    print("="*80)
    print(f"开始时间: {results['start_time']}")

    # 1. LanceDB POC 测试
    run_lancedb_tests()

    # 2. Daft POC 测试
    run_daft_tests()

    # 3. 生成总结
    generate_summary()

    # 4. 最终报告
    print("\n" + "="*80)
    print("🎉 Day 2 POC 验证完成！")
    print("="*80)
    print(f"\n✅ 总测试数: {results['summary']['total_tests']}")
    print(f"✅ 成功率: {results['summary']['success_rate']}")
    print(f"\n完成时间: {results['summary']['end_time']}")

    return 0


if __name__ == "__main__":
    import sys
    sys.exit(main())
