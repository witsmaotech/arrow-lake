# DIntelliHub - POC 测试目录

此目录包含 Sprint 1 Week 2 的所有 POC 验证测试代码和结果。

## 📁 目录结构

```
poc/
├── README.md              # 本文件
├── data/                  # 测试数据目录
│   ├── raw/              # 原始数据
│   ├── processed/        # 处理后数据
│   └── vectors/          # 向量数据
├── scripts/              # 测试脚本
│   ├── generate_data.py  # 数据生成脚本
│   ├── daft_poc.py       # Daft POC 测试
│   └── lancedb_poc.py    # LanceDB POC 测试
└── results/              # 测试结果
    ├── daft/             # Daft 测试结果
    └── lancedb/          # LanceDB 测试结果
```

## 🚀 快速开始

### 1. 生成测试数据
```bash
cd poc/scripts
python generate_data.py --size 10GB --type parquet
```

### 2. 运行 Daft POC
```bash
python daft_poc.py --input /data/raw --output /data/processed
```

### 3. 运行 LanceDB POC
```bash
python lancedb_poc.py --count 100000 --dimension 128
```

## 📊 测试配置

### Daft POC 配置
- 数据量: 10GB
- 文件格式: Parquet
- 处理目标: < 30分钟
- 内存限制: 16GB

### LanceDB POC 配置
- 向量数量: 100K
- 向量维度: 128
- 索引类型: IVF_PQ
- 搜索延迟: < 100ms (P99)
- 准确率: > 90%

## 📈 测试结果

测试结果将保存在 `results/` 目录，包括：
- 性能指标（时间、吞吐、延迟）
- 数据样本
- 日志文件
- 图表和可视化

## 🔗 相关文档

- [Week 2 POC 计划](../docs/phase1-mvp/sprint1-infrastructure/week2-poc-plan.md)
- [Sprint Plan](../docs/phase1-mvp/sprint1-infrastructure/sprint-plan.md)
- [Day 3 成功报告](../docs/phase1-mvp/sprint1-infrastructure/DAY3-SUCCESS-REPORT.md)
