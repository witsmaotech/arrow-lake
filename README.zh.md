# Arrow Lake — 统一多模态数据湖仓平台

> 生产级多模态数据湖仓。Lance + Daft + Ray 构建，2822 测试，78% 覆盖率，零高危安全漏洞。

## 项目概述

Arrow Lake 是一个面向多模态数据（文本、图像、音频、视频）的统一数据湖仓平台。基于 DARMU 技术栈（Daft + Arrow + Ray + Metaflow + MinIO + LanceDB），提供从数据摄取、质量管控、向量搜索、知识图谱到数据血缘的全链路能力。

### 核心能力

- **多模态摄取** — 文本/图像/音频/视频/PDF，支持批量与 HTTP 写入
- **向量搜索** — 语义检索 + 混合搜索（BM25 + 向量），IVF_PQ 索引
- **知识图谱** — HugeGraph 集成：图谱构建、Gremlin 查询、GraphRAG 联合问答
- **RAG 管线** — 多 LLM Provider、会话历史、引用溯源、流式输出
- **文档管线** — PDF 解析 → 分块 → 向量化 → Lance，OCR 回退
- **数据质量** — Schema 验证、空值检测、内容去重（SHA-256 + pHash）
- **数据血缘** — 全链路 Lineage 追踪与 SQL 查询
- **审计追踪** — HMAC-SHA256 防篡改审计日志
- **安全防护** — RBAC 三级权限、JWT 黑名单、Gremlin 注入防御、路径穿越防护
- **数据导出** — Parquet / CSV 格式导出，版本选择与列投影
- **分布式计算** — Redis 分布式 Session、Ray 分布式摄取、Metaflow 工作流
- **可观测性** — structlog + Prometheus 指标 + OpenTelemetry 集成
- **REST API** — 40+ 端点，OpenAPI 文档，API Key + JWT 认证，速率限制
- **CLI 工具** — `arrow-lake` 命令行界面，支持摄取、查询、管理等操作

## 架构

```
┌─────────────────────────────────────────────────────────────────────┐
│                         Arrow Lake SDK (Lake)                        │
├──────┬──────┬──────┬──────┬──────┬──────┬──────┬──────┬────────────┤
│Ingest│Search│Quality│Export│Catalog│ KG   │ RAG  │CLI   │  Server   │
├──────┴──────┴──────┴──────┴──────┴──────┴──────┴──────┴────────────┤
│           Core Layer (Config / Exceptions / Metrics / Auth)         │
├──────────────────────────┬──────────────────────────────────────────┤
│       Storage            │          Runtime                         │
│  LanceDB + MinIO/S3      │   Ray + Metaflow + Redis + HugeGraph     │
└──────────────────────────┴──────────────────────────────────────────┘
```

### 模块结构

```
arrow_lake/
├── __init__.py          # Lake SDK 入口
├── _version.py          # 版本信息 (v1.3.0)
├── config/              # Pydantic 配置（YAML + 环境变量 + Redis）
├── cli/                 # Click CLI
├── api/                 # FastAPI REST API (40+ 端点, RBAC)
│   ├── auth_service.py  # JWT + API Key 认证, 黑名单 LRU
│   ├── middleware.py     # 安全头, GZip, 速率限制
│   └── routers/         # 15 个路由模块 (RBAC 守卫)
├── ingest/              # 数据摄取 (CSV/JSON/Parquet/HTTP/图像/视频/PDF)
├── storage/             # 存储抽象层 (LanceDB + MinIO/S3)
├── query/               # 查询引擎 (DuckDB OLAP + Redis 信号量)
├── search/              # 向量/全文/混合/分面搜索
├── catalog/             # 数据目录 + Lineage 血缘追踪
├── knowledge_graph/     # HugeGraph 客户端 + 图谱构建 + GraphRAG
├── rag/                 # RAG 管线 (多 Provider + 会话 + 流式)
├── embed/               # 嵌入生成 (HuggingFace + Ollama)
├── ops/                 # 备份/恢复 + 数据导出
├── workflow/            # 审计日志 (HMAC 验证) + Metaflow
├── quality/             # 数据质量 (验证 + 去重)
├── ray_runtime/         # Ray 分布式运行时
├── sdk/                 # SDK 工具集
└── testing/             # 测试工具 (fixtures + mocks)
```

## 快速开始

### 安装

```bash
uv venv && source .venv/bin/activate
uv sync
```

### 配置

```bash
cp .env.example .env
# 按需修改 .env 和 configs/dev.yaml
```

### 启动基础设施

```bash
docker compose -f deploy/docker-compose.yml up -d
```

服务：MinIO (S3)、Redis、Grafana、Prometheus、Jaeger。
知识图谱：使用外部 HugeGraph 部署（`docker network connect` 桥接网络）。

### 基本用法

```python
from arrow_lake import Lake

lake = Lake.from_yaml("configs/dev.yaml")

# 摄取数据
report = lake.ingest("my_dataset", ["data/data.csv"])

# 向量搜索
results = lake.search("my_dataset", query_vector=[0.1, 0.2, ...], top_k=10)

# SQL 分析
result = lake.olap_query("my_dataset", "SELECT category, COUNT(*) FROM my_dataset GROUP BY category")

# 知识图谱
task_id = await lake.kg_build("my_dataset")
stats = await lake.kg_stats()

# RAG 问答
answer = await lake.rag_query("什么是向量数据库？", dataset_name="docs")
```

### CLI

```bash
arrow-lake ingest files my_data data.parquet
arrow-lake search fts my_data --query "搜索内容" --top-k 5
arrow-lake export my_data --output result.parquet
arrow-lake status
```

## 生产部署

### Docker Compose

```bash
docker compose -f deploy/docker-compose.yml up -d
```

### Kubernetes (Helm)

```bash
helm install arrow-lake deploy/helm/arrow-lake/
```

生产安全特性：
- **RBAC**: VIEWER / EDITOR / ADMIN 三级权限，覆盖 40+ API 端点
- **Redis**: 分布式 Session 协调 + JWT 黑名单持久化
- **TLS**: 可配置 TLS 终止 + 安全响应头 (CSP, HSTS, X-Frame-Options)
- **Helm**: Deployment / HPA / CronJob 备份 / Ingress / PDB / Secret 模板
- **审计**: HMAC-SHA256 防篡改审计日志，启动时强制密钥校验
- **NetworkPolicy**: 限制 Pod 间通信

## 测试

```bash
# 全量测试 (2822 tests)
uv run pytest tests/ -q

# 分批运行
uv run pytest tests/unit/ tests/api/ -q     # 单元 + API (2539)
uv run pytest tests/integration/ tests/e2e/ -q  # 集成 + E2E (283)

# 覆盖率
uv run pytest tests/ --cov=arrow_lake --cov-report=term-missing
```

覆盖率 78%，Bandit 安全扫描零 HIGH。

## 技术栈

| 层 | 技术 |
|---|---|
| 数据处理 | Daft 0.7.8, PyArrow 23.0.1 |
| 向量存储 | LanceDB 0.30.2 |
| 分布式计算 | Ray 2.54.1 |
| 工作流 | Metaflow 2.19.22 |
| SQL 查询 | DuckDB 1.5.1 |
| 知识图谱 | HugeGraph 1.7.0 (hstore) |
| Session 协调 | Redis 7.4 (分布式信号量) |
| 对象存储 | MinIO (S3-compatible) |
| HTTP API | FastAPI, httpx, Click (CLI) |
| 安全 | JWT + API Key, RBAC, HMAC 审计 |
| AI 嵌入 | PyTorch, sentence-transformers |
| 可观测性 | structlog, Prometheus, OpenTelemetry |
| 配置管理 | Pydantic v2, PyYAML |

## 部署结构

```
deploy/
├── docker-compose.yml    # 全栈服务编排
├── helm/arrow-lake/      # Helm Chart (K8s 生产部署)
│   └── templates/        # Deployment / HPA / Ingress / PDB / Secret / CronJob
├── grafana/              # Grafana 仪表板
├── monitoring/           # Prometheus 配置
└── minio-init/           # MinIO 初始化脚本
```

## 文档

- [Cookbook 教程](docs/cookbook/README.md) — 13 章 + 43 个示例
- [安全策略](SECURITY.md) — 认证、RBAC、审计、传输安全
- [贡献指南](CONTRIBUTING.md) — 开发环境与编码规范
- [更新日志](CHANGELOG.md) — 版本变更记录

## 许可证

MIT License — Copyright (c) 2026 Witshine
