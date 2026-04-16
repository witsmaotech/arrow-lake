# Arrow Lake — 统一多模态数据湖仓平台

> Lance + Daft + Ray 构建的高性能多模态数据湖仓，80 个功能 Story，1414 个测试。

## 项目概述

Arrow Lake 是一个面向多模态数据（文本、图像、音频、视频）的统一数据湖仓平台。基于 DARMU 技术栈（Daft + Arrow + Ray + Metaflow + MinIO + LanceDB），提供从数据摄取、质量管控、向量搜索到数据血缘的全链路能力。

### 核心能力

- **多模态摄取** — 文本/图像/音频/视频，支持批量与流式写入
- **向量搜索** — 语义检索 + 混合搜索（BM25 + 向量），支持多模态索引
- **数据质量** — Schema 验证、空值检测、内容去重（SHA-256 + pHash）
- **数据血缘** — 全链路 Lineage 追踪与 SQL 查询
- **数据导出** — Parquet / CSV 格式导出，支持版本选择与列投影
- **分布式计算** — Ray 分布式摄取、Metaflow 工作流编排
- **可观测性** — structlog + Prometheus 指标 + OpenTelemetry 集成
- **CLI 工具** — `arrow-lake` 命令行界面，支持摄取、查询、管理等操作

## 架构

```
┌────────────────────────────────────────────────────────────────────┐
│                         Arrow Lake SDK (Lake)                      │
├──────┬──────┬──────┬──────┬──────┬──────┬──────┬──────┬───────────┤
│Ingest│Search│Quality│Export│Catalog│Schema │Audit │CLI  │  Server  │
├──────┴──────┴──────┴──────┴──────┴──────┴──────┴──────┴───────────┤
│                     Core Layer (Config / Exceptions / Metrics)     │
├──────────────────────────┬─────────────────────────────────────────┤
│       Storage            │          Runtime                        │
│  LanceDB + MinIO/S3      │       Ray + Metaflow                    │
└──────────────────────────┴─────────────────────────────────────────┘
```

### 模块结构

```
arrow_lake/
├── __init__.py          # Lake SDK 入口
├── config.py            # Pydantic 配置（YAML + 环境变量）
├── cli.py               # Click CLI
├── server.py            # HTTP API 服务
├── _version.py          # 版本信息
├── metrics.py           # Prometheus 指标
├── exceptions.py        # 统一异常体系
├── core/                # 核心抽象（Schema、数据模型）
├── ingest/              # 数据摄取（批量、流式、MinIO）
├── storage/             # 存储抽象层
├── query/               # 查询引擎（向量、全文、分面、导出）
├── quality/             # 数据质量（验证、去重、异常检测）
├── catalog/             # 数据目录（血缘、Actor 管理）
├── embed/               # 嵌入生成（多模型、批量）
├── workflow/            # 工作流（审计日志、Metaflow 集成）
├── ray_runtime/         # Ray 分布式运行时
├── sdk/                 # SDK 工具集
└── testing/             # 测试工具（fixtures、mocks）
```

## 快速开始

### 安装

```bash
# 创建虚拟环境
uv venv && source .venv/bin/activate

# 安装依赖
uv sync

# 可选：去重感知哈希支持
uv sync --extra dedup
```

### 配置

```bash
# 复制环境变量模板
cp .env.example .env

# 编辑配置
# 按需修改 .env 和 configs/dev.yaml
```

### 启动基础设施

```bash
docker compose -f deploy/docker-compose.yml up -d
```

启动的服务：MinIO (S3)、PostgreSQL、Redis、Grafana、Prometheus。

### 基本用法

```python
from arrow_lake import Lake

lake = Lake.from_yaml("configs/dev.yaml")

# 摄取数据（CSV, JSON, Parquet 文件）
report = lake.ingest("my_dataset", ["data/data.csv", "data/data.json"])

# 向量搜索（需要嵌入向量）
results = lake.search("my_dataset", query_vector=[0.1, 0.2, ...], top_k=10)

# 数据质量过滤
report = lake.quality_filter("my_dataset")

# 数据导出
lake.export("my_dataset", "output/data.parquet")
```

### CLI

```bash
arrow-lake ingest --source data.parquet --dataset my_data
arrow-lake search --dataset my_data --query "搜索内容" --top-k 5
arrow-lake export --dataset my_data --format parquet --output result.parquet
arrow-lake status
```

## 测试

```bash
# 全量测试（1414 tests）
uv run pytest tests/ -q

# 仅单元测试
uv run pytest tests/unit/ -q

# 仅集成测试
uv run pytest tests/integration/ -q

# 带覆盖率
uv run pytest tests/ --cov=arrow_lake --cov-report=term-missing
```

测试覆盖 80 个功能 Story，覆盖率达 82%+。

## 技术栈

| 层 | 技术 |
|---|---|
| 数据处理 | Daft 0.7.8, PyArrow 23.0.1 |
| 向量存储 | LanceDB 0.30.2 |
| 分布式计算 | Ray 2.54.1 |
| 工作流 | Metaflow 2.19.22 |
| SQL 查询 | DuckDB 1.5.1 |
| 对象存储 | MinIO (S3-compatible) |
| 配置管理 | Pydantic v2, PyYAML |
| 可观测性 | structlog, Prometheus, OpenTelemetry |
| HTTP | httpx, Click (CLI) |
| AI 嵌入 | PyTorch, sentence-transformers |

## 部署

```
deploy/
├── compose/             # Docker Compose 服务定义
├── grafana/             # Grafana 仪表板
├── helm/                # Helm Chart（K8s）
├── monitoring/          # Prometheus 配置
├── minio-init/          # MinIO 初始化脚本
└── scripts/             # 部署脚本
```

## 许可证

MIT License — Copyright (c) 2026 Witshine
