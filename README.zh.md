# Arrow Lake — 生产级多模态数据湖仓平台

> Lance + Daft + Ray 构建，面向 AI/ML 团队的统一多模态数据湖仓。
> 文本、图像、音频、向量、知识图谱 — 一个平台全覆盖。

[![Tests](https://img.shields.io/badge/tests-2872%20passing-brightgreen)](https://gitee.com/wits__sunpw/wits-infra-dintellihub)
[![Coverage](https://img.shields.io/badge/coverage-80%25-green)](https://gitee.com/wits__sunpw/wits-infra-dintellihub)
[![Security](https://img.shields.io/badge/bandit-0%20HIGH-success)](https://gitee.com/wits__sunpw/wits-infra-dintellihub)
[![Python](https://img.shields.io/badge/python-3.11+-blue)](https://gitee.com/wits__sunpw/wits-infra-dintellihub)
[![Version](https://img.shields.io/badge/version-1.6.0-blue)](https://gitee.com/wits__sunpw/wits-infra-dintellihub)
[![License](https://img.shields.io/badge/license-MIT-informational)](https://gitee.com/wits__sunpw/wits-infra-dintellihub)

## 项目概述

Arrow Lake 是一个面向多模态数据（文本、图像、音频、视频）的统一数据湖仓平台。基于 Lance 列式存储 + Daft DataFrame + Ray 分布式计算，提供从数据摄取、质量管控、多模态搜索、OLAP 分析到 RAG 和知识图谱的全链路能力。

### 核心能力

| 能力 | 说明 |
|---|---|
| **向量搜索** | Cosine/L2/Dot 相似度，IVF_PQ / IVF_FLAT / IVF_HNSW_PQ 索引 |
| **全文搜索** | Tantivy + jieba 中文分词，词干提取，停用词过滤 |
| **混合搜索** | RRF（Reciprocal Rank Fusion）融合向量 + 文本得分 |
| **分面搜索** | 多列元数据过滤，可配置分面维度 |
| **集成搜索** | 跨多向量列的 RRF 融合检索 |
| **OLAP 分析** | DuckDB SQL：GROUP BY、窗口函数、JOIN、流式执行 |
| **Daft DataFrame** | 延迟求值 + Ray 分布式执行 |
| **知识图谱** | HugeGraph 集成：图谱构建、Gremlin 查询、GraphRAG |
| **RAG 管线** | 多 LLM Provider（OpenAI, Anthropic, vLLM, Ollama, DeepSeek），会话历史、引用溯源、流式输出 |
| **文档管线** | PDF → 分块 → 向量化 → Lance，7 种分块策略，OCR 回退 |
| **数据质量** | Schema 验证、空值检测、去重（精确哈希 + 感知哈希）、NeMo Curator |
| **数据血缘** | 全链路 Lineage 追踪 + SQL 查询 |
| **审计追踪** | HMAC-SHA256 防篡改审计日志 |
| **安全防护** | RBAC 三级权限、Redis JWT 黑名单、速率限制、Gremlin 注入防御、路径穿越防护 |
| **元数据治理** | Gravitino 1.2.1 元数据联邦: DuckDB ↔ Lance Catalog 双向同步、Tags 标签分类、Policies 保留/脱敏策略 |
| **REST API** | 40+ 端点 + `/metadata/*` 代理端点 (catalogs/tables/tags/policies/statistics/models)，API Key + JWT 认证，TLS，安全响应头 |
| **分布式** | Redis 分布式信号量、Ray 分布式摄取、GPU 自动伸缩、Helm Chart |

## 架构

```
┌─────────────────────────────────────────────────────────────────────┐
│                         Arrow Lake SDK (Lake)                        │
│   8 Mixin Classes: Ingest | Search | Query | Admin | Lineage |     │
│                    Audit | RAG | Knowledge Graph                     │
├─────────────────────────────────────────────────────────────────────┤
│  API Layer: FastAPI REST (40+) | CLI (16+ commands) | Python SDK   │
├──────────────────────────┬──────────────────────────────────────────┤
│    Query Layer           │          Intelligence Layer              │
│  Vector | FTS | Hybrid  │   RAG Pipeline (Multi-LLM)              │
│  Faceted | Ensemble     │   Knowledge Graph (HugeGraph + GraphRAG) │
│  OLAP SQL | Daft        │                                          │
├──────────────────────────┴──────────────────────────────────────────┤
│    Storage Layer: LanceDB + MinIO/S3/GCS + DuckDB + Redis          │
└─────────────────────────────────────────────────────────────────────┘
```

## 快速开始

### 安装

```bash
pip install arrow-lake

# 常用扩展
pip install "arrow-lake[fts,rag,document,chunking-full,jupyter]"
```

### 试用 Demo

```bash
arrow-lake demo
```

自带合成数据的交互式 Demo — 向量搜索、SQL 分析、全文搜索，约 15 秒完成。零配置。

### 基本用法

```python
from arrow_lake import Lake

# 本地模式，零配置
lake = Lake("./my_lake")

# 摄取数据
import pyarrow as pa
table = pa.table({"text": ["机器学习", "深度学习", "数据分析"]})
lake.create_dataset("articles", table)

# SQL 分析
result = lake.olap_query("articles", "SELECT * FROM articles")

# YAML 配置模式（生产环境）
lake = Lake.from_yaml("configs/prod.yaml")
lake.ingest("docs", ["data/papers/"])
lake.embed_and_add("docs")

# RAG 问答
answer = await lake.rag_query("什么是向量数据库？", dataset_name="docs")
```

### CLI

```bash
arrow-lake demo                    # 交互式 Demo
arrow-lake serve                   # 启动 REST API
arrow-lake ingest files my_data    # 文件摄取
arrow-lake search vector my_data --query "ML" --top-k 5
arrow-lake query sql my_data --sql "SELECT * FROM my_data LIMIT 10"
arrow-lake kg build my_data        # 构建知识图谱
arrow-lake rag query "什么是RAG？" --dataset docs
```

## 生产部署

```bash
# Docker Compose（11 服务，Profile 激活）
docker compose -f deploy/docker-compose.yml up -d                # core
docker compose --profile dev -f deploy/docker-compose.yml up -d  # + Ray + Jupyter
docker compose --profile gravitino -f deploy/docker-compose.yml up -d  # + Gravitino + Lance REST

# Kubernetes（Helm）
helm install arrow-lake deploy/helm/arrow-lake/
```

生产安全特性：
- **RBAC**: VIEWER / EDITOR / ADMIN 三级权限，覆盖全部 40+ API 端点
- **认证**: 双模式 API Key + JWT (HS256/RS256)，Redis 黑名单 + TTL
- **Redis**: 分布式 Session 协调 + JWT 黑名单持久化 + 分布式信号量
- **TLS**: 可配置 TLS 终止 + 安全响应头 (CSP, HSTS, X-Frame-Options)
- **Helm**: Deployment / HPA（CPU + 自定义指标） / CronJob 备份（每日 02:00） / Ingress / PDB / Secret / NetworkPolicy
- **审计**: HMAC-SHA256 防篡改审计日志
- **NetworkPolicy**: 限制 Pod 间通信（Redis 6379, HugeGraph 8080, HTTPS 443, DNS 53）
- **容器加固**: cap-drop ALL、只读文件系统、资源限制、PID 约束

## 测试

```bash
# 全量测试（2872 tests，80%+ 覆盖率）
pytest tests/ -q

# 分批运行
pytest tests/unit/ tests/api/ -q          # 单元 + API
pytest tests/integration/ tests/e2e/ -q   # 集成 + E2E

# 覆盖率
pytest tests/ --cov=arrow_lake --cov-report=term-missing
```

## 技术栈

LanceDB + Daft + Ray + DuckDB + PyArrow + FastAPI + HugeGraph + Redis + Metaflow + Gravitino

| 层 | 技术 | 版本 |
|---|---|---|
| 数据处理 | Daft, PyArrow | 0.7.8, 23.0.1 |
| 向量存储 | LanceDB, Lance | 0.30.2 |
| OLAP 引擎 | DuckDB | 1.5.2 |
| 分布式计算 | Ray, Metaflow | 2.54.1, 2.19.22 |
| 元数据治理 | Gravitino, Lance REST Catalog | 1.2.1 |
| 知识图谱 | HugeGraph | 1.7.0 |
| Session / 缓存 | Redis (hiredis) | >=5.0 |
| 对象存储 | MinIO / S3 / GCS | boto3 >=1.35 |
| HTTP API | FastAPI, Uvicorn, slowapi | >=0.115 |
| LLM Provider | OpenAI, Anthropic, vLLM, Ollama, DeepSeek | — |
| 全文搜索 | Tantivy, jieba | >=0.20.0 |
| 嵌入模型 | Qwen3-Embedding-0.6B, sentence-transformers | — |
| 安全 | PyJWT, HMAC-SHA256 | >=2.9 |
| 可观测性 | structlog, Prometheus, OpenTelemetry | — |
| 配置管理 | Pydantic v2, pydantic-settings, PyYAML | >=2.7 |

## 文档

- [Cookbook 教程](docs/cookbook/README.md) — 13 章 + 43 个示例（中英双语）
- [产品介绍](docs/arrow-lake-product-introduction-zh.html) — 完整产品概览
- [安全策略](SECURITY.md) — 认证、RBAC、审计、传输安全
- [贡献指南](CONTRIBUTING.md) — 开发环境与编码规范
- [更新日志](CHANGELOG.md) — 版本变更记录

## 许可证

MIT License — Copyright (c) 2026 Witshine
