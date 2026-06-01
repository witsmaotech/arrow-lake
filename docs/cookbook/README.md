# Arrow Lake Cookbook

Arrow Lake 数据湖平台实战教程，涵盖数据摄取、搜索、分析、RAG、知识图谱和部署运维。

## 教程目录 / Table of Contents

| #  | 中文                                           | English                                               |
| -- | -------------------------------------------- | ----------------------------------------------------- |
| 01 | [快速入门](./01-quickstart-zh.md)                | [Quick Start](./01-quickstart.md)                     |
| 02 | [数据摄取](./02-ingestion-zh.md)                 | [Data Ingestion](./02-ingestion.md)                   |
| 03 | [配置系统](./03-configuration-zh.md)             | [Configuration](./03-configuration.md)                |
| 04 | [向量搜索与索引](./04-vector-search-zh.md)          | [Vector Search & Indexing](./04-vector-search.md)     |
| 05 | [全文搜索](./05-fulltext-search-zh.md)           | [Full-Text Search](./05-fulltext-search.md)           |
| 06 | [混合搜索与分面搜索](./06-hybrid-faceted-zh.md)       | [Hybrid & Faceted Search](./06-hybrid-faceted.md)     |
| 07 | [OLAP 分析](./07-olap-analytics-zh.md)         | [OLAP Analytics](./07-olap-analytics.md)              |
| 08 | [RAG 问答管线](./08-rag-pipeline-zh.md)          | [RAG Pipeline](./08-rag-pipeline.md)                  |
| 09 | [知识图谱与 GraphRAG](./09-knowledge-graph-zh.md) | [Knowledge Graph & GraphRAG](./09-knowledge-graph.md) |
| 10 | [REST API 指南](./10-rest-api-zh.md)           | [REST API Guide](./10-rest-api.md)                    |
| 11 | [数据质量与去重](./11-quality-dedup-zh.md)          | [Quality & Deduplication](./11-quality-dedup.md)      |
| 12 | [部署与运维](./12-deployment-zh.md)               | [Deployment & Operations](./12-deployment.md)         |
| 13 | [CLI 完全参考手册](./13-cli-reference.md)          | CLI Complete Reference Manual                         |
| 14 | [工作流编排](./14-workflow-orchestration.md)       | [Workflow Orchestration](./14-workflow-orchestration.md) |
| 15 | [Gravitino 元数据治理](./15-gravitino-metadata-zh.md) | [Gravitino Metadata Governance](./15-gravitino-metadata.md) |

## 学习路径 / Learning Path

**入门** → 01 → 02 → 03

**搜索进阶** → 04 → 05 → 06

**分析 & AI** → 07 → 08 → 09

**生产部署** → 10 → 11 → 12

## 环境要求 / Prerequisites

* Python 3.11+
* `uv sync` 安装依赖
* (可选) MinIO / S3 对象存储
* (可选) HugeGraph 图数据库 (GraphRAG 需要)
* (可选) Ollama / OpenAI API (RAG 需要)

## 示例数据 / Sample Data

教程中引用的示例数据位于 [`datas/`](./datas/README.md) 目录，包含论文 CSV/PDF、交易记录、知识库 JSONL、示例图片和视频，可直接运行。

## 版本

对应 Arrow Lake v1.5.2

## v1.5.2 新特性 — Security Hardening & Code Quality

| #  | 说明 |
| -- | ---- |
| — | JWT 空密钥阻止启动、Kerberos 命令注入消除、SQL 注入参数化 |
| — | Redis 移除默认密码、所有端口 127.0.0.1 绑定、SSRF 防护 |
| — | Admin bypass 改用 Role enum、Refresh token 旋转撤销 |
| — | SQL/Gremlin 注入防护增强、OLAP 端点增加安全校验 |
| — | 53 项 lint 清理、Bandit HIGH 清零 |

## v1.5.1 新特性 — Security Governance + Lineage

| #  | 说明 |
| -- | ---- |
| — | Gravitino Auth Providers: Simple/OAuth2/Kerberos/Null 四种认证 |
| — | Lineage Hooks: 摄入/搜索/查询自动血缘记录 |
| — | Schema-level ACL + Deny-first 权限模型 |
| — | 跨 Catalog 联邦查询下推优化 |

## v1.5.0 新特性 — Platform Systematization

| #  | 说明 |
| -- | ---- |
| — | CLI 场景别名: knowledge / connect / search / manage / explore |
| — | docs_v2 三层文档体系: Data / Knowledge / Compute Plane |
| — | 架构可视化 + 安全审计 + 术语表 |
| — | BMAD Agent System 集成 (20+ 产品/架构/开发 Agent) |

## v1.4.4 新特性 — RAG Quality Leap

| #  | 说明 |
| -- | ---- |
| — | RAG Reranking Pipeline: CrossEncoder / LLM / Noop 三种重排策略 |
| — | Query Transformation: HyDE / MultiQuery / Identity 查询改写 |
| — | Multi-turn Conversation: 对话历史注入 + Token 预算管理 |
| — | CLI High Performance: Lake 实例缓存 + Embedding 缓存 + Rich 进度条 |
| — | Observability: OpenTelemetry tracing + Latency breakdown tracking |

## v1.4.3 新特性 — Production Readiness

| #  | 说明 |
| -- | ---- |
| — | OpenTelemetry 分布式链路追踪集成 |
| — | Alertmanager 多渠道告警通知 |
| — | Auto-Maintenance 后台定时维护 |
| — | Quality Gates 数据摄入质量门控 |

## v1.4.2 新特性 — 安全加固

| #  | 说明 |
| -- | ---- |
| — | FQN 注入防护 (`ValidationMixin`) |
| — | JSON 反序列化深度限制 + 类型白名单 |
| — | Thread Zombie 僵尸线程检测与回收 |

## v1.4.1 新特性 — Gravitino 元数据治理

| #  | 文件 | 说明 |
| -- | ---- | ---- |
| 15 | `cookbook/15-gravitino-metadata.md` | Gravitino 元数据治理：Metalake/Catalog/Schema 管理、标签治理、策略引擎、统计采集、模型注册、RBAC 桥接、定时同步 |

> 新增功能：Apache Gravitino 元数据联邦集成，实现跨数据源统一元数据管理、标签治理、策略执行、统计采集、ML 模型注册与 RBAC 权限桥接。

## v1.3.4 新特性

| #  | 示例                                                       | 说明                                            |
| -- | -------------------------------------------------------- | ----------------------------------------------- |
| 40 | `deployment/40_redis_distributed_session.py`             | Redis 分布式会话：DuckDB 连接池信号量 + JWT 黑名单 |
| 41 | `security/41_rbac_role_matrix.py`                        | RBAC 角色矩阵：VIEWER / EDITOR / ADMIN 端点权限控制 |
| 42 | `deployment/42_hpa_autoscaling.py`                       | HPA 自动扩缩容：Kubernetes HorizontalPodAutoscaler |
| 43 | `admin/43_cronjob_backup.py`                             | CronJob 备份自动化：定时备份 + 历史清理 + 告警     |

> 新增功能：Redis 分布式会话协调、30+ 端点 RBAC 权限控制、Kubernetes HPA 自动扩缩容、CronJob 定时备份、生产安全清单（TLS / CSP / NetworkPolicy）。

### REST API 示例 (examples_api/)

| #  | 示例文件 | 说明 |
| -- | ------- | ---- |
| 01-05 | `health_auth_datasets` ~ `embedding_index` | 核心功能：健康检查、认证、摄取、搜索、嵌入 |
| 06-10 | `rag_pipeline` ~ `multimodal_ingest` | AI 管线：RAG、知识图谱、质量去重、血缘审计、多模态 |
| 11-20 | `transaction_analytics` ~ `cross_dataset` | 业务场景：交易分析、论文库、知识库、销售漏斗、视频分析等 |
| 21-32 | `daft_dataframe_basics` ~ `daft_pivot_explode_sample` | Daft DataFrame：基础查询、清洗、关联、时序、SQL、pivot 等 |
