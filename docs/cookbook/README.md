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
| 13 | [CLI 完全参考手册](./13-cli-reference.md)          | [CLI Complete Reference](./13-cli-reference-en.md)   |
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

## 服务依赖示例（需外部服务）

以下示例依赖外部服务（LLM / HugeGraph / OCR / MinIO）。**在主机 `.venv` 运行前请 export 对应端点**（默认指向 localhost 会失败；api 容器已预配这些端点）。

| 示例 | 依赖 | 需设置的环境变量 |
|---|---|---|
| `19_knowledge_graph_build` | LLM + HugeGraph | `ARROW_LAKE__EMBEDDING__API_BASE` + `ARROW_LAKE__HUGEGRAPH__HOST` / `PORT` |
| `20_rag_qa_system` | LLM + MinIO | `ARROW_LAKE__EMBEDDING__API_BASE` + MinIO 凭证（`AWS_ACCESS_KEY_ID` / `AWS_SECRET_ACCESS_KEY`） |
| `21_document_ingest` | OCR (paddleocr) | 安装 `pip install paddleocr`，并按需配 PDF/OCR 后端 |
| `31_graphrag_qa` | LLM 嵌入 | `ARROW_LAKE__EMBEDDING__API_BASE` |
| `34_rag_streaming` | LLM | `ARROW_LAKE__EMBEDDING__API_BASE` |

主机运行（Ollama 端点按实际改）：

```bash
export ARROW_LAKE__EMBEDDING__API_BASE="http://10.100.93.100:11434/v1"
export ARROW_LAKE__HUGEGRAPH__HOST="localhost"
export ARROW_LAKE__HUGEGRAPH__PORT="8089"
.venv/bin/python docs/cookbook/examples/19_knowledge_graph_build.py
```

或在 api 容器内运行（已预配全部服务端点）：

```bash
docker exec -it deploy-api-1 .venv/bin/python /app/examples/19_knowledge_graph_build.py
```

> 其余 38 个示例为 self-contained（仅依赖本地 `datas/`），无需外部服务即可直接运行。

## 版本

对应 Arrow Lake v1.9.10

## v1.7.0 新特性 — Hyper-Extract KG + Doc-Type 路由

| #  | 示例 | 说明 |
| -- | ---- | ---- |
| 44 | [`examples/44_kg_doctype_he.py`](./examples/44_kg_doctype_he.py) | 文档类型路由：三层路由（override→gallery→default）+ 别名归一 + LLM 内容推断；he 抽取后端启用 |
| 45 | [`examples/45_kg_doctype_api.py`](./examples/45_kg_doctype_api.py) | REST API 构建 KG 全流程：摄入→KG→状态→统计；doc_type/he 在 API 模式说明 |
| — | — | HugeGraph PD 集群模式（运行时多图，每文档独立 KG 隔离） |
| — | — | A 方案实体双写（通用 `entity` 顶点 + 细分 label）+ 关系路由 |
| — | — | ingest `doc_type` 贯通：上传 API → facade → Ingestor → chunk → KG builder |

> 新增功能：hyper-extract 抽取后端（精准三元组）、doc_type 三层路由（config override → TemplateGallery 元数据匹配 → default 兜底）+ LLM 内容推断、HugeGraph PD 集群运行时多图隔离、A 方案实体双写。详见 [v1.7.0 方案](../v1.7.0-hyper-extract-kg-extraction-plan.md) 与 [CHANGELOG](../../CHANGELOG.md)。

## v1.8 / v1.9 新特性 — 控制面统一 / 多模态 / RAG 质量 / 治理兑现

| 版本    | 说明 |
| ------- | ---- |
| v1.9.0  | **Turso/libSQL 统一控制面**：RBAC / 身份 / personal_token / catalog / 任务历史 / RAG 会话全部走 libSQL；fail-close 鉴权（401） |
| v1.9.2  | **多模态深化**：以图搜图（`POST /embed/image` + IVF_PQ）、导出统一（datasets/detail/search）、DuckLake 物化视图、Pivot 助手、纯 SVG 图表 |
| v1.9.3  | **数据集字段注释**：`field_comments`（PyArrow 直读 parquet/CSV sidecar）+ `GET/POST /schema/annotate` 原位写回 |
| v1.9.4  | **血缘 actor 贯穿**（delete 审计 + 列级血缘）+ KG `project_concept_graph` 模板（22 类型 + 14 关系，百炼 qwen-turbo 100% def）+ MERGE_FIELD 单模式 |
| v1.9.5  | **RAG 质量全链路**：`default_retrieval_strategy=hybrid` 真生效、`faithfulness` 防幻觉校验、GraphRAG 三路并行（延迟 -40~50%）、qa_llm 两阶段、`OllamaReranker`（默认 Qwen3-Reranker-0.6B） |
| v1.9.6  | **KG 质量/性能**（snap 编辑距离归一 + strict 空定义过滤 + KA LRU 缓存）+ **治理兑现**（`lineage.html` 列级血缘、masking 4 函数 + HMAC fail-fast + mask-preview、audit 复用 Lance）+ **安全 fail-closed**（masking/RBAC/Gravitino 全 fail-closed） |

> 详见 [v1.9.6 发布说明](../arrow-lake-v1.9.6-release-zh.md) 与 [RAG 质量方案](../v1.9.5-rag-quality-plan.md)。

## v1.6.3 新特性 — Deploy Hardening & nginx Proxy

| #  | 说明 |
| -- | ---- |
| — | 示例脚本支持 nginx HTTPS 代理模式 (`ARROW_LAKE_BASE_URL` / `ARROW_LAKE_SSL_VERIFY`) |
| — | HugeGraph Gremlin 绑定修复，`g.V()` 开箱即用 |
| — | Redis/MinIO/基础设施 Prometheus 告警规则 (+8 rules) |
| — | nginx gzip + CSP + buffer 性能优化 |
| — | 镜像标签固定，Redis 密码泄露修复，`.env.example` 脱敏模板 |

## v1.6.2 新特性 — Redis 任务共享

| #  | 说明 |
| -- | ---- |
| — | Redis HASH 双写 + 跨 worker 任务状态可见性 |

## v1.6.1 新特性 — 性能修复

| #  | 说明 |
| -- | ---- |
| — | threading.RLock 死锁修复 + kg_build fire-and-forget |

## v1.6.0 新特性 — 基础夯实

| #  | 说明 |
| -- | ---- |
| — | docker-compose.prod.yml 版本对齐 arrow-lake:1.6.0, metrics 端口 9091 |

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
