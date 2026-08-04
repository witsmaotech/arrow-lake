<div align="center">

# Arrow Lake

**面向 AI 的开源多模态数据湖仓。**

向量 · 全文 · SQL 分析 · 知识图谱 · GraphRAG · 文档 AI —— **一个自托管平台**，而非五个工具拼凑而成。

[![Version](https://img.shields.io/badge/version-1.10.0-blue)](#)
[![License](https://img.shields.io/badge/license-Apache--2.0-informational)](LICENSE)
[![Python](https://img.shields.io/badge/python-3.11%2B-blue)](#)
[![Tests](https://img.shields.io/badge/tests-6%2C100%2B-brightgreen)](#)
[![Coverage](https://img.shields.io/badge/coverage-90%25-brightgreen)](#)
[![REST routes](https://img.shields.io/badge/REST-186%20routes-orange)](#)

**仓库:** [Gitee](https://gitee.com/wits__sunpw/wits-infra-dintellihub) · [GitHub 镜像](https://github.com/Witshine/arrow-lake) _（创建后请调整镜像 URL）_

**中文** | [English](README.md)

</div>

---

## 什么是 Arrow Lake

Arrow Lake 是一个**生产级多模态数据湖仓**，专为**企业 AI 团队与数据平台**打造。无需把向量数据库、OLAP 引擎、图存储、LLM/RAG 框架、治理层以及 UI 分别拼接起来 —— Arrow Lake 将它们统一在**一个 `Lake` facade** 之后，并开箱即用地提供共享存储、RBAC、血缘与审计。

它是**自托管优先**的：你的数据、你的模型、你的网络。可通过 `pip`、Docker Compose 或 Kubernetes (Helm) 运行。

<p align="center">
  <img src="docs/architecture-design/diagrams/01-layered-architecture.svg" alt="Arrow Lake 分层架构" width="760">
</p>

## 为什么选择 Arrow Lake

如今大多数 AI 数据栈都是**胶水代码** —— 用 LanceDB 做向量、DuckDB 跑 SQL、HugeGraph 存图、LangChain 搞 RAG，再手搓一套 auth/governance/UI 层把它们包起来。Arrow Lake 把这些收拢为单一平台：

| 拼凑的痛点 | Arrow Lake 提供的 |
|---|---|
| 5 个数据存储 + 5 个客户端 + 5 套 auth 模型 | **一个 `Lake` facade**，一个存储层，一套 RBAC 模型 |
| "这条查询该用向量/SQL/图哪个工具?" | 在**同一数据集**上统一搜索 + SQL + 图 |
| 忽略领域结构的 RAG | **GraphRAG + 可插拔抽取模板**（v1.10.0） |
| 没有血缘、没有审计、没有治理 | **内置血缘、HMAC 审计、Gravitino 治理** |
| 只有后端、没有 UI | **16+ 页运维控制台**（admin、KG 可视化、OLAP worksheet、血缘、模板 QA） |

**四点核心竞争力：**

1. **统一而非组装** —— 向量 + 全文 + SQL + 图 + RAG 共享同一 facade、同一存储、同一 auth/血缘/审计平面。
2. **原生 GraphRAG** —— HugeGraph + hyper-extract 知识抽取，配合**模板驱动建图**，运行时加载新模板（**无需重建镜像、无需重启**）。
3. **真正的多模态** —— 文本、图像、音频、向量，外加 Docling 文档解析与以图搜图。不只是文本嵌入。
4. **默认生产就绪** —— RBAC、JWT、限流、防篡改审计、Helm chart、可观测性。不是开发玩具。

## 核心能力

| 领域 | 能力 |
|---|---|
| 🔎 **搜索** | 向量（cosine/L2/dot；IVF_PQ/IVF_FLAT/IVF_HNSW_PQ）、Tantivy 全文（jieba 中文分词）、**hybrid RRF**、faceted、跨列 ensemble 融合 |
| 🧠 **RAG** | 多 provider LLM（OpenAI/Anthropic/vLLM/Ollama/DeepSeek/Bailian）、会话、流式、citation、**cross-encoder / LLM / Ollama 重排**、faithfulness 校验、HyDE / MultiQuery、多轮 |
| 🕸️ **知识图谱 & GraphRAG** | HugeGraph：build、Gremlin 查询、最短路径、k-neighbor；**GraphRAG** 注入 KG 上下文；**基于模板的抽取**（hyper-extract） |
| 🧩 **抽取模板（v1.10.0）** | CRUD 控制台 + AI 辅助编写 + 数据集绑定 + dry-run + **模板质量验证 harness**（生成文档 → 建图 → 可视化 → RAG → 清理）；`category↔doc_type` 动态字典 |
| 📊 **分析** | DuckDB OLAP（窗口、JOIN、流式、物化视图）+ Daft 惰性 DataFrame（Ray 分布式） |
| 📄 **文档 AI** | PDF/Office/HTML → Docling 解析 → 切块 → 嵌入 → Lance；7 种切块策略；OCR 回退；以图搜图 |
| 🛡️ **安全与治理** | RBAC（VIEWER/EDITOR/ADMIN）、JWT + API-key 双重认证、Redis 黑名单、限流、**HMAC-SHA256 审计链**、Gravitino 1.3.0 联邦（tags/policies/model catalog）、**列级脱敏** |
| 🖥️ **控制台** | 16+ 页面：数据集、KG 可视化、OLAP SQL worksheet、血缘图、抽取模板管理、模板 QA、admin、审计、治理 |
| 🚀 **运维** | Docker Compose（基于 profile）、**Helm chart**（HPA、Ingress、PDB、NetworkPolicy、CronJob 备份）、OpenTelemetry + Prometheus + Grafana |

## 控制台

Arrow Lake 内置原生运维控制台（原生 JS，由 API 提供服务）—— 无需独立部署前端：

- **数据集** —— 浏览、预览（分页 + 搜索）、带字段注释的 schema、导出
- **知识图谱** —— 交互式 `vis-network` 图谱、带 citation 的 GraphRAG 问答
- **OLAP Worksheet** —— DuckDB SQL + Daft DataFrame + Pivot 助手，受 RBAC 约束
- **血缘** —— DAG 可视化 + 事件历史
- **抽取模板** —— YAML CRUD、AI 生成、dry-run、质量验证（基于模板建图并在其上跑 RAG）
- **Admin / 审计 / 治理** —— 用户、RBAC、防篡改审计、tags 与脱敏策略

<table>
<tr>
<td align="center"><b>首页总览</b><br><img src="docs/asserts/images/页面-首页.png" width="420"></td>
<td align="center"><b>知识图谱</b><br><img src="docs/asserts/images/页面-知识图谱01.png" width="420"></td>
</tr>
<tr>
<td align="center"><b>OLAP 数据分析</b><br><img src="docs/asserts/images/页面-数据分析olap.png" width="420"></td>
<td align="center"><b>模板质量验证（v1.10.0）</b><br><img src="docs/asserts/images/页面-图谱抽取模板质量验证01.png" width="420"></td>
</tr>
<tr>
<td align="center"><b>RAG 问答</b><br><img src="docs/asserts/images/页面-RAG.png" width="420"></td>
<td align="center"><b>数据血缘</b><br><img src="docs/asserts/images/页面-数据血缘.png" width="420"></td>
</tr>
</table>

<sup>共 19 张截图，见 [`docs/asserts/images/`](docs/asserts/images/) —— 含登录、数据集、摄入、数据准备、清洗整理、索引嵌入、异步任务、文档教程等。</sup>

## 快速开始（30 秒）

```bash
pip install arrow-lake
arrow-lake demo        # 自包含 demo：在合成数据上跑向量 + SQL + 全文搜索
```

或用 Python：

```python
from arrow_lake import Lake
import pyarrow as pa

lake = Lake("./my_lake")           # 本地文件系统存储 —— 无需 MinIO、无需 Docker

table = pa.table({
    "id": ["1", "2", "3"],
    "text": ["machine learning", "deep learning", "data analytics"],
})
lake.create_dataset("articles", table)

# 向量搜索、全文、hybrid、SQL —— 全在同一数据集上
print(lake.search("articles", query="ML", top_k=3))
```

从 `pip install` 到拿到第一条结果，不到一分钟。

## 对比

| 能力 | Arrow Lake | LanceDB | DuckDB | Milvus / Qdrant | Dify | LangChain |
|---|:---:|:---:|:---:|:---:|:---:|:---:|
| 向量 / hybrid 搜索 | ✅ | ✅ | — | ✅ | — | — |
| SQL OLAP 分析 | ✅ | — | ✅ | — | — | — |
| 知识图谱 + GraphRAG | ✅ | — | — | — | 部分 | 部分 |
| 模板驱动抽取 | ✅ | — | — | — | — | — |
| 文档 AI（Docling、多模态） | ✅ | 部分 | — | — | 部分 | 部分 |
| 元数据治理（Gravitino） | ✅ | — | — | — | — | — |
| RBAC + 审计 + 血缘（内置） | ✅ | — | — | 部分 | 部分 | — |
| 运维控制台 | ✅ | — | — | 部分 | ✅ | — |
| **统一单一平台** | ✅ | 向量 | OLAP | 向量 | LLM 应用 | 框架 |

以上每一个工具在其专长领域都很优秀。Arrow Lake 面向需要**全部能力且已集成**、又不想维护胶水代码的团队。

## 使用场景

- **企业文档 RAG** —— 摄取 PDF/Office，构建知识图谱，带 citation 与溯源作答
- **多模态搜索** —— 文→图、图→图、跨模态 hybrid 检索
- **GraphRAG / 知识平台** —— 领域专属抽取模板、结构化实体关系图
- **自助式分析** —— 在驱动搜索与 RAG 的同一湖仓上跑 SQL + DataFrame
- **平台级 AI 数据层** —— 为内部 AI 产品提供单一受治理、可审计、受 RBAC 保护的后端

## 安装

**pip**（库 / 单节点）
```bash
pip install "arrow-lake[fts,rag,he,document]"
```

**Docker Compose**（完整栈，基于 profile）
```bash
git clone <repo> && cd wits-infra-dintellihub
docker compose -f deploy/docker-compose.prod_minimal.yml up -d
# API: http://127.0.0.1:8000  ·  Console: http://127.0.0.1:8000/console/
```

**Kubernetes**（生产）
```bash
helm install arrow-lake deploy/helm/arrow-lake/
```

## CLI

```bash
arrow-lake demo                        # 交互式 demo
arrow-lake serve                       # REST API 服务
arrow-lake ingest files my_data *.csv
arrow-lake search vector my_data --query "ML" --top-k 5
arrow-lake query sql my_data --sql "SELECT * FROM my_data LIMIT 10"
arrow-lake kg build my_data            # 构建知识图谱
arrow-lake kg build my_data --template project_concept_graph   # v1.10.0 模板覆盖
arrow-lake rag query "..." --dataset docs
```

## 配置

27 个独立配置段，3 层优先级：**defaults → 环境变量（`ARROW_LAKE__` 前缀）→ YAML**。

```python
lake = Lake("./data")                       # 本地，最小化
lake = Lake.from_yaml("configs/prod.yaml")  # 生产
```

## 文档

- 📖 [Cookbook](docs/cookbook/README.md) —— 15 章、45+ 示例（中英双语）
- 🏗️ [架构](docs/ARCHITECTURE.md) —— 权威技术参考
- 🎨 [产品介绍](docs/arrow-lake-product-introduction.md) —— 能力概览
- 🔒 [安全策略](SECURITY.md) —— auth、RBAC、审计、传输
- 🤝 [贡献指南](CONTRIBUTING.md) —— 开发环境与规范
- 📒 [更新日志](CHANGELOG.md) —— 版本历史
- 🌐 [API 文档](http://localhost:8000/docs) —— OpenAPI/Swagger（运行实例）

## 项目状态

稳定且已在生产中使用。当前版本：**v1.10.0**（知识抽取模板管理 —— M1–M5）。完整历史与路线图方向（更深的多模态、分布式横向扩展、更多抽取后端）见 [CHANGELOG](CHANGELOG.md)。

- **6,100+ 测试**，90%+ 覆盖率，零高危安全发现（bandit）
- **186 条 REST 路由**，跨 22 个 router
- trunk-based 开发，频繁发布

## 社区

- 💬 [Issues / 问答](https://gitee.com/wits__sunpw/wits-infra-dintellihub/issues)
- 🤝 [贡献指南](CONTRIBUTING.md) · [行为准则](CODE_OF_CONDUCT.md)
- 💼 **欢迎商业支持 / 咨询 / 定制集成** —— 通过 Issues 联系。

欢迎贡献（代码、文档、模板、bug 报告）。非平凡的改动请先开 issue 讨论。

## 许可证

[Apache License 2.0](LICENSE) —— © 2026 Witshine.

Apache-2.0 允许你自由使用、修改和分发 Arrow Lake（包括商业用途），只需保留署名与许可证声明。为采纳而生 —— 也为随之而来的咨询/项目而生。
