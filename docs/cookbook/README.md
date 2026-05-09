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

对应 Arrow Lake v1.3.0

## v1.3.0 新特性

| #  | 示例                                                       | 说明                                            |
| -- | -------------------------------------------------------- | ----------------------------------------------- |
| 40 | `deployment/40_redis_distributed_session.py`             | Redis 分布式会话：DuckDB 连接池信号量 + JWT 黑名单 |
| 41 | `security/41_rbac_role_matrix.py`                        | RBAC 角色矩阵：VIEWER / EDITOR / ADMIN 端点权限控制 |
| 42 | `deployment/42_hpa_autoscaling.py`                       | HPA 自动扩缩容：Kubernetes HorizontalPodAutoscaler |
| 43 | `admin/43_cronjob_backup.py`                             | CronJob 备份自动化：定时备份 + 历史清理 + 告警     |

> 新增功能：Redis 分布式会话协调、30+ 端点 RBAC 权限控制、Kubernetes HPA 自动扩缩容、CronJob 定时备份、生产安全清单（TLS / CSP / NetworkPolicy）。
