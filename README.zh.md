<div align="center">

# Arrow Lake

**面向 AI 的开源多模态数据湖仓。**

向量 · 全文检索 · SQL 分析 · 知识图谱 · GraphRAG · 文档智能 ——
**一个自托管平台**，而非五件套拼接。

[![Version](https://img.shields.io/badge/version-1.10.4-blue?style=flat-square)](#)
[![License](https://img.shields.io/badge/license-Apache--2.0-informational?style=flat-square)](LICENSE)
[![Python](https://img.shields.io/badge/python-3.11%2B-blue?style=flat-square)](#)
[![Tests](https://img.shields.io/badge/tests-6%2C100%2B-brightgreen?style=flat-square)](#)
[![Coverage](https://img.shields.io/badge/coverage-90%25%2B-brightgreen?style=flat-square)](#)
[![REST](https://img.shields.io/badge/REST-186%20routes%20·%2022%20routers-orange?style=flat-square)](#)
[![LanceDB](https://img.shields.io/badge/LanceDB-0.36.0-9cf?style=flat-square)](#)
[![DuckDB](https://img.shields.io/badge/DuckDB-1.5.5-9cf?style=flat-square)](#)

**仓库：** [Gitee](https://gitee.com/wits__sunpw/wits-infra-dintellihub) · [GitHub 镜像](https://github.com/witsmaotech/arrow-lake)

**[English](README.md)** | 中文

<p align="center">
  <img src="docs/assets/images/页面-首页.png" alt="Arrow Lake console overview" width="800">
</p>

</div>

---

## Arrow Lake 是什么

Arrow Lake 是一个**生产级多模态数据湖仓**，面向**企业 AI 团队与数据平台**而构建。它将向量检索、全文检索、SQL 分析、知识图谱与 RAG 引擎统一在**一个 `Lake` facade** 之后 —— 存储共享、RBAC、血缘与审计开箱即用。

核心理念很简单：今天的多数 AI 数据栈本质都是**胶水代码** —— 这边一个向量库，那边一个 OLAP 引擎，再加一个图存储、一个 LLM 框架，外裹一层手写的鉴权/治理/UI。Arrow Lake 把这种碎片化收敛为一个平台，**向量、全文、SQL、图与 RAG 查询在同一份数据集上运行**，并由同一套身份与审计平面治理。

它是**自托管优先**的：你的数据、你的模型、你的网络、你的合规边界。可通过 `pip` 单节点部署，通过 Docker Compose 拉起全栈，或通过 Helm chart 部署到 Kubernetes。零厂商锁定、零数据外泄、零按席位授权 —— Apache-2.0 协议，为规模化采用而生。

<p align="center">
  <img src="docs/architecture-design/diagrams/01-layered-architecture.svg" alt="Arrow Lake layered architecture" width="800">
</p>

---

## ✨ 核心能力

Arrow Lake 围绕**六大支柱**组织。每一根支柱都是一等子系统 —— 而非薄薄一层封装 —— 它们共享同一存储层、同一身份模型与同一审计链路。

### 🗄️ 统一湖仓

**一个存储层、一个 facade、一个治理平面。** 单个 `Lake` 对象通过一致的 Python API 暴露数据集、检索、SQL、图与 RAG —— 同样的操作也可经 186 条 REST 路由完成。存储采用 Lance（列式、多模态、可落对象存储或本地 FS），因此向量、文本、图像与结构化字段在同一张表中并存。你不必再同时维护五套客户端、五个鉴权模型与五份部署清单。

```python
from arrow_lake import Lake
lake = Lake("./my_lake")
# 一个 facade → 向量检索、SQL、全文、图、RAG、治理。
```

### 🔎 混合检索

**向量 + Tantivy 全文 + RRF 融合 —— 混合是默认项，而非事后补丁。**

- **向量检索**支持 cosine / L2 / dot 度量与**多种索引类型**（`IVF_PQ`、`IVF_HNSW_PQ`、`IVF_FLAT`、`IVF_SQ`、`IVF_HNSW`），小数据集走暴力扫描。
- **全文检索**基于 Tantivy BM25，并集成 **jieba 分词**以适配 CJK / 中文文本。
- **混合检索**采用 Reciprocal Rank Fusion（RRF）—— 推荐的默认策略 —— 融合语义与词法信号。
- **分面检索（Faceted search）**用于下钻导航，外加面向多字段检索的**跨列集成融合**。

同一份索引、同一份数据集即可服务上述三种模式。无需独立的检索集群，无需维护第二份存储保持同步。

### 🕸️ GraphRAG 与知识图谱

**HugeGraph + hyper-extract 抽取 —— 一张真正的图，而非三元组袋子。**

- 数据集级隔离的知识图谱（`kg_{dataset}`）运行于 HugeGraph 之上，支持 Gremlin、最短路径与 k-neighbor 遍历。
- **GraphRAG** 将实体邻居上下文注入 LLM prompt，并做 **`relation_type` 富化**，让模型看到实体之间*如何*连接，而不仅仅是它们*是否*相连。
- **模板驱动的图谱构建**由 [hyper-extract](https://github.com/hyper-extract) 驱动 —— 强类型 Knowledge Abstract、8 种 auto-type（graph、temporal、hypergraph、spatial……）以及 80+ 领域模板。
- 内置**实体归一化**与**启发式孤儿连接**，让图保持连通与可用。

### 🧩 知识抽取模板 —— v1.10.0 ⚑

**运行时编写、绑定与验证抽取模板 —— 无需重建镜像、无需重启。** 这是本版本的头部新特性。

- **动态加载** —— 丢一个 YAML 模板进去即被实时拾取；镜像从不重建，服务从不重启。
- **Console CRUD** —— 从 Web UI 创建、编辑、列举与描述模板。
- **数据集绑定** —— 把模板绑定到数据集，使 `kg build` 自动采用（`category↔doc_type` 动态词典）。
- **AI 辅助编写** —— 从一句 prompt 生成模板草稿，再行精修。
- **试运行（Dry-run）** —— 在提交到全量构建前用样本文本测试模板。
- **质量验证 harness** —— 生成一份合成文档 → 构建图 → 可视化 → 其上做 RAG → 拆除，全在同一个页面完成。在模板上线前量化 orphan rate、关系类型覆盖率与平均度数。

### 💬 生产级 RAG

**多 provider、默认混合、带重排与抗幻觉校验。**

- **多 provider LLM** —— OpenAI、Anthropic、vLLM、Ollama、DeepSeek 与百炼（阿里云 MaaS）均为一等公民。
- **混合检索为默认**策略（向量 + FTS 经 RRF 融合）。
- **三大 reranker 家族** —— cross-encoder、LLM-judge 与 **Ollama 托管的 Qwen3-Reranker**（可完全离线运行）。
- **Faithfulness 校验**以抑制幻觉答案。
- **HyDE** 与 **MultiQuery** 查询扩展、多轮 **session**、**流式**响应，以及带溯源的落地 **citation**。

### 🖼️ 多模态与文档智能

**文本、图像、音频、视频与文档 —— 摄入、解析、嵌入、可检索。**

- **Docling** 文档解析（PDF / Office / HTML），含版面、表格与结构抽取 —— GPU 加速。
- **以图搜图**经 CLIP 风格嵌入实现，并支持以文搜图、以文搜文。
- 面向不同内容形态的 **7 种分块（chunking）策略**。
- **OCR 回退**与结构化表格抽取，适配扫描件。

### 📊 分析

**DuckDB OLAP + Daft 惰性 DataFrame —— 从一次性查询到 Ray 分布式管道。**

- **DuckDB** 负责窗口函数、JOIN、流式聚合，以及在 Lance 表上的**物化视图**。
- **Daft** 惰性 DataFrame 承载 Ray 分布式、out-of-core 负载 —— 批推理、大规模变换与 join。
- 提供 **Pivot 助手**与 `SUMMARIZE` 用于快速探索性分析。

### 🛡️ 治理与安全

**企业级控制内建其中，而非外挂补丁。**

- **RBAC** 含 VIEWER / EDITOR / ADMIN 角色，JWT + API-key **双因子认证**，以及 Redis 支撑的 token 黑名单。
- **HMAC-SHA256 防篡改审计链路**与列级**数据脱敏**。
- **Gravitino 1.3.0** 联邦 —— 跨异构数据源的 tag、policy、模型 catalog 与留存规则。
- **`system_db`**（libSQL）控制面承载身份、RBAC、personal token、任务历史与 RAG 会话。
- **Helm chart**（HPA、Ingress、PDB、NetworkPolicy、CronJob 备份）与 **OpenTelemetry + Prometheus + Grafana** 可观测性。

---

## 为什么选 Arrow Lake

多数 AI 数据栈由五件专业工具拼接而成，每一件单看都很出色，彼此却互不感知。Arrow Lake 用一个全集成平台替代那层胶水代码。

### 为什么 RAG 质量不同

纯向量 RAG（LangChain / LlamaIndex + 向量库）按嵌入相似度检索 —— 它返回的是*段落*，对实体间如何关联一无所知。Arrow Lake 的 **GraphRAG** 注入实体邻居上下文并做 **`relation_type` 富化**，让模型看到实体之间*如何*连接（导致、包含、引用）—— 而非仅仅共现。在实体/关系密集型问题（法规、事件链、组织架构）上，这正是"泛泛总结"与"精准可溯源答案"的差别。

它由**模板驱动抽取**（强类型 Knowledge Abstract、80+ 领域模板、运行时加载 —— 无需重建）与**质量验证 harness**（在模板上线前量化 orphan rate、关系类型覆盖率、平均度数）支撑 —— 把 KG 质量从"凭感觉"变成"可度量的闸门"。

| 拼接五件套的痛 | Arrow Lake 给你的 |
|---|---|
| 5 套数据存储 + 5 套客户端 + 5 套鉴权模型 | **一个 `Lake` facade**、一个存储层、一套 RBAC 模型 |
| "这条查询该走向量 / SQL / 图哪个工具？" | 在同一份数据集上做**统一的检索 + SQL + 图** |
| RAG 无视你的领域结构 | **GraphRAG + 运行时可插拔的抽取模板** |
| 没有血缘、没有审计、没有治理 | **内建血缘、HMAC 审计、Gravitino 治理** |
| 只有后端、没有 UI | **19 页运维 console**（admin、KG 可视化、OLAP、血缘、模板质检） |

**四点让它具备竞争力：**

1. **统一，而非拼装** —— 向量 + 全文 + SQL + 图 + RAG 共享一个 facade、一份存储、一套鉴权/血缘/审计平面。
2. **原生 GraphRAG** —— HugeGraph + hyper-extract 抽取，模板驱动建图并支持运行时加载新模板（无需重建镜像、无需重启）。
3. **真正的多模态** —— 文本、图像、音频、向量、Docling 文档解析与以图搜图。而不只是文本嵌入。
4. **默认即生产可用** —— RBAC、JWT、限流、防篡改审计、Helm chart 与可观测性。不是仅供开发的玩具。

---

## 🖥️ Console

Arrow Lake 自带一个**原生运维 console**（原生 JS，由 API 直接 serve）—— 无需独立前端部署，无需处理 CORS。

- **数据集** —— 浏览、预览（分页 + 检索）、带字段注释的 schema、导出
- **知识图谱** —— 交互式 `vis-network` / G6 图可视化、带 citation 的 GraphRAG 问答
- **OLAP 工作表** —— DuckDB SQL + Daft DataFrame + Pivot 助手，受 RBAC 约束
- **血缘** —— DAG 可视化 + 事件历史
- **抽取模板** —— YAML CRUD、AI 生成、试运行，以及**质量验证 harness**（从模板建图并做 RAG）
- **Admin / 审计 / 治理** —— 用户、RBAC、防篡改审计、tag 与脱敏策略

<table>
<tr>
<td align="center"><b>总览</b><br><img src="docs/assets/images/页面-首页.png" width="420"></td>
<td align="center"><b>知识图谱</b><br><img src="docs/assets/images/页面-知识图谱01.png" width="420"></td>
</tr>
<tr>
<td align="center"><b>OLAP 工作表</b><br><img src="docs/assets/images/页面-数据分析olap.png" width="420"></td>
<td align="center"><b>模板质检（v1.10.0）</b><br><img src="docs/assets/images/页面-图谱抽取模板质量验证01.png" width="420"></td>
</tr>
<tr>
<td align="center"><b>RAG 问答</b><br><img src="docs/assets/images/页面-RAG.png" width="420"></td>
<td align="center"><b>血缘</b><br><img src="docs/assets/images/页面-数据血缘.png" width="420"></td>
</tr>
</table>

<sup>共 19 张截图见 [`docs/assets/images/`](docs/assets/images/) —— 含登录、数据集、摄入、数据准备、清洗整理、索引/嵌入、异步任务、文档等。</sup>

---

## 🚀 快速开始（30 秒）

```bash
pip install arrow-lake
arrow-lake demo        # 自包含 demo：在合成数据上跑向量 + SQL + 全文检索
```

或在 Python 中：

```python
from arrow_lake import Lake
import pyarrow as pa

lake = Lake("./my_lake")           # 本地 FS 存储 —— 无需 MinIO、无需 Docker

table = pa.table({
    "id": ["1", "2", "3"],
    "text": ["machine learning", "deep learning", "data analytics"],
})
lake.create_dataset("articles", table)

# 向量检索、全文、混合、SQL —— 全在同一份数据集上
print(lake.search("articles", query="ML", top_k=3))
```

从 `pip install` 到拿到第一条结果，不到一分钟。

---

## 📊 横向对比

| 能力 | Arrow Lake | LanceDB | DuckDB | Milvus / Qdrant | Dify | LangChain |
|---|:---:|:---:|:---:|:---:|:---:|:---:|
| 向量 / 混合检索 | ✅ | ✅ | — | ✅ | — | — |
| SQL OLAP 分析 | ✅ | — | ✅ | — | — | — |
| 知识图谱 + GraphRAG | ✅ | — | — | — | partial | partial |
| 模板驱动抽取 | ✅ | — | — | — | — | — |
| 文档智能（Docling、多模态） | ✅ | partial | — | — | partial | partial |
| 元数据治理（Gravitino） | ✅ | — | — | — | — | — |
| RBAC + 审计 + 血缘（内建） | ✅ | — | — | partial | partial | — |
| 运维 console | ✅ | — | — | partial | ✅ | — |
| **统一单一平台** | ✅ | 向量 | OLAP | 向量 | LLM 应用 | 框架 |

上述每一款工具在其专长领域都很出色。Arrow Lake 面向的是需要**全部能力且彼此集成**、又不愿维护胶水代码的团队。

---

## 🎯 适用场景

- **企业文档 RAG** —— 摄入 PDF / Office 文档，构建知识图谱，带 citation 与溯源回答
- **多模态检索** —— 文 → 图、图 → 图，以及跨模态的混合检索
- **GraphRAG / 知识平台** —— 领域专属抽取模板产出结构化实体-关系图
- **自助式分析** —— 在驱动检索与 RAG 的同一座湖上跑 SQL + DataFrame
- **平台级 AI 数据层** —— 为内部 AI 产品提供一个受治理、可审计、受 RBAC 保护的后端
- **受治理的数据产品** —— 经 Gravitino 跨异构数据源打 tag、脱敏、留存与审计

**在真实大数据集上验证：**

- **`ontime` —— 107M 行，美国航班准点数据。** 分析型 SQL（COUNT / GROUP BY / ORDER BY）在 pyarrow 下耗时 **43s**，native scan 仅 **0.3s**（**145×**）。单个自托管节点即可交互式服务航空延误与航线性能分析 —— 无需 OLAP 集群。
- **`noaa_china` —— 气象观测数据。** 嵌套 `struct` 类型 location 展平为 `longitude`/`latitude`，清洗写回吞吐稳态 **10M+ rows/s**，随后用于地理气候分析与时序 SQL。
- **大文档 RAG —— 500+ 页 PDF。** Docling GPU 解析（RTX 3090 上 ~1s/页）+ 混合检索 + GraphRAG，从上传到带溯源引用的答案端到端打通。

---

## 📊 性能基准

`tests/benchmark/` 下的一套 `@pytest.mark.benchmark` 基准在真实代码（非 mock）上测量每条热路径：摄入、向量 / 全文 / 混合搜索、KG 构建、RAG，以及本次新增的四项基准 —— **OLAP 分析 SQL**、**文档分块**、**清洗写回**、**混合负载并发**。用 `bash deploy/scripts/run_critical_benchmarks.sh` 跑完整 11 步套件，或单文件 `.venv/bin/pytest tests/benchmark/test_bench_<名称>.py -m benchmark -s`。

> **环境**：Python 3.11.14 · WSL2 Linux x86_64 · 10 核 · DuckDB 1.5.5 · pylance 9.0.0 · lancedb 0.36.0。数值为多次重复的中位数（`BenchmarkReport`）。绝对值随硬件变化，**形态**（时间花在哪、吞吐在哪见顶）才是可复用的结论。

**OLAP 分析查询** —— `OlapSearchBridge.query`（即 `/query/olap` 路径），在合成的 `ontime` schema 上：

| 查询形态 | 1 万行 | 10 万行 |
|---|---|---|
| 过滤 + 排序 + LIMIT | 0.178 s（56K 行/s） | 0.183 s（546K 行/s） |
| 按航司 GROUP BY | 0.176 s（57K 行/s） | 0.180 s（554K 行/s） |
| 航线拼接 + HAVING | 0.190 s（53K 行/s） | 0.189 s（530K 行/s） |
| 多键 GROUP BY 年×月 | 0.183 s（55K 行/s） | 0.176 s（568K 行/s） |

数据放大 10 倍**延迟几乎不变** —— 每查询约 180 ms 的 bridge 固定开销（注册 Lance → DuckDB 视图 → SELECT → Arrow）占绝对主导，而 DuckDB 扫描 + 聚合本身在 10 万行以内近乎免费。吞吐因此随行数线性扩展（56K → 554K 行/s）。

**大规模 OLAP —— `ontime` 107M 行（真实美国航班数据集）：**

| 查询 | pyarrow_fallback | **native scan** | 加速 |
|---|---|---|---|
| COUNT(*) 全扫 | 43.4 s | **0.3 s** | **145×** |
| GROUP BY DayOfWeek（7 组） | 40.7 s | **1.0 s** | **40×** |
| GROUP BY Origin（382 组） | 51.3 s | **1.5 s** | **34×** |
| ORDER BY LIMIT 100（107M 排序） | 56.8 s | **3.1 s** | **18×** |

native lance scan 把聚合 / 谓词 / LIMIT 下推到 Rust scanner（零拷贝 —— 不再每查询物化 9.8GB）。按数据集 opt-in（`lance_scan_mode_overrides`），并由 **D-state 熔断器**守护 —— 重复卡顿时自动降回 pyarrow。复现：`tests/benchmark/olap_ontime_benchmark.py`。

**文档分块** —— `DocumentChunker.chunk`，摄入管线 CPU 前端：

| 负载 | 吞吐 |
|---|---|
| 递归（20 页，512/50） | ~37K 页/s（~185K 块/s → 100 块） |
| 按页策略 | ~1.16M 页/s |
| 按段策略 | ~560K 页/s |
| 递归（100 页） | ~38K 页/s（500 块） |
| chunk_size 256 / 512 / 1024 | ~34–38K 页/s（200 / 100 / 40 块） |

分块永远不会成为摄入瓶颈 —— 递归切分约 37K 页/s，`chunk_size` 只改变块数，不影响吞吐。

**清洗 / 写回** —— `POST /clean` 路径（读取 → DuckDB 转换 → `restore_dataset`）：

| 阶段 | 1 万行 | 10 万行 |
|---|---|---|
| 完整 读取→转换→写回 | 0.023 s（436K 行/s） | 0.059 s（1.68M 行/s） |
| 读取数据集 | 2.43M 行/s | 4.10M 行/s |
| DuckDB 转换 | 915K 行/s | 5.77M 行/s |
| `restore_dataset` 写回 | 1.70M 行/s | 9.45M 行/s |

写回超线性扩展（436K → 1.68M 行/s）；10 万行时没有单一阶段成为瓶颈。

**混合负载并发** —— 300 次操作（向量 / 全文 / OLAP 各 100）在 `ThreadPoolExecutor` worker 扫描下：

| Worker 数 | QPS | 墙钟时间 |
|---|---|---|
| 1 | 8.3 | 36.4 s |
| 5 | 10.2 | 29.3 s |
| 10 | 10.4 | 29.0 s |
| 20 | 10.4 | 28.8 s |

吞吐在 **5 worker 时即见顶于 ~10 QPS** —— 再加并发毫无收益。这是单节点上同步查询层的争用天花板（GIL + DuckDB 会话池 + Lance 扫描），也正是异步查询演进（v1.8.0 #17）的实证依据。

---

## 📚 文档与 Cookbook

### Cookbook（中英双语）

Cookbook 是首要的实战指南 —— **共 20 章（00–19）**，90 个可运行示例。

| # | 章节 | SDK 示例 | REST 示例 |
|---|---|---|---|
| 00 | [总览（从这里开始）](docs/cookbook/00-overview.md) | — | — |
| 01 | [快速开始](docs/cookbook/01-quickstart.md) | — | — |
| 02 | [数据摄入](docs/cookbook/02-ingestion.md) | `01_ingest_basics.py` | `02_ingest_file_http.py` |
| 03 | [配置](docs/cookbook/03-configuration.md) | — | — |
| 04 | [向量检索与索引](docs/cookbook/04-vector-search.md) | `02_search_and_index.py` | `03_search_vector_fts_hybrid.py` |
| 05 | [全文检索](docs/cookbook/05-fulltext-search.md) | — | — |
| 06 | [混合与分面检索](docs/cookbook/06-hybrid-faceted.md) | `23_faceted_search.py` | — |
| 07 | [OLAP 分析](docs/cookbook/07-olap-analytics.md) | `03_olap_and_export.py` | `04_olap_export_backup.py` |
| 08 | [RAG 管道](docs/cookbook/08-rag-pipeline.md) | `20_rag_qa_system.py` | `06_rag_pipeline.py` |
| 09 | [知识图谱与 GraphRAG](docs/cookbook/09-knowledge-graph.md) | `19_knowledge_graph_build.py` | `07_knowledge_graph.py` |
| 10 | [REST API 指南](docs/cookbook/10-rest-api.md) | — | （全部 `examples_api/`） |
| 11 | [质量与去重](docs/cookbook/11-quality-dedup.md) | `04_quality_and_dedup.py` | `08_quality_dedup.py` |
| 12 | [部署与运维](docs/cookbook/12-deployment.md) | — | — |
| 13 | [CLI 完整参考](docs/cookbook/13-cli-reference.md) | — | — |
| 14 | [工作流编排](docs/cookbook/14-workflow-orchestration.md) | — | — |
| 15 | [Gravitino 元数据治理](docs/cookbook/15-gravitino-metadata.md) | `08_catalog_management.py` | — |
| 16 | [v1.8.0 新特性](docs/cookbook/16-v1.8.0-new-features-zh.md) | — | — |
| 17 | [数据脱敏](docs/cookbook/17-data-masking.md) | — | — |
| 18 | [血缘可视化](docs/cookbook/18-lineage-visualization.md) | — | `09_lineage_audit.py` |
| 19 | [REST 食谱](docs/cookbook/19-rest-recipes.md) | — | — |

> **学习路径：** 入门 01→02→03 · 检索 04→05→06 · AI 07→08→09 · 生产 10→11→12

**可运行示例** —— [`docs/cookbook/examples/`](docs/cookbook/examples/) 下 51 个 SDK 脚本，[`docs/cookbook/examples_api/`](docs/cookbook/examples_api/) 下 39 个 REST 脚本，包含 v1.10.0 的**模板管理**脚本（`examples/46_template_management.py`、`examples_api/34_extraction_templates_api.py`）。

### 参考文档

- 🏗️ [**架构**](docs/architecture-design/ARCHITECTURE.md) —— 权威技术参考（17 章：分层、facade、数据流 + 8 图集 + 附录 A–E）
- 📦 [产品介绍](docs/arrow-lake-product-introduction.md) —— 能力总览
- 🔒 [安全策略](SECURITY.md) —— 鉴权、RBAC、审计、传输
- 🤝 [贡献指南](CONTRIBUTING.md) —— 开发环境与规范
- 📒 [更新日志](CHANGELOG.md) —— 版本历史
- 🌐 [API 文档](http://localhost:8000/docs) —— OpenAPI / Swagger（运行中的实例）
- 📖 [Cookbook 目录](docs/cookbook/README.md) —— 完整章节索引

---

## 安装

**pip**（库 / 单节点）
```bash
pip install "arrow-lake[fts,rag,he,document]"
```

**Docker Compose**（全栈，基于 profile）
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

34 个独立配置段，四层优先级：**默认值 → `.env` → 环境变量（`ARROW_LAKE__` 前缀）→ YAML**。

```python
lake = Lake("./data")                       # 本地、最小化
lake = Lake.from_yaml("configs/prod.yaml")  # 生产
```

---

## 项目状态

稳定且已在生产中使用。当前版本：**v1.10.4** —— v1.10.0 知识抽取模板管理（M1–M5：动态加载、CRUD、绑定、AI 编写、试运行、质量验证 harness）+ v1.10.1 稳定性与治理加固（docling GPU triton JIT 修复、KG 模板降级路径、配置精简、examples 整合进 cookbook）+ **v1.10.2 文本文档增量构建（ingest/KA/KG 增量）、性能基准套件扩充、超时/可靠性加固（OLAP `conn.interrupt()` 看门狗避免卡住扫描拖死连接池、异步任务心跳 + 僵尸回收避免 worker 死亡后任务永久卡 running、ingest 全链路补 per-call timeout），并复测 SSD 性能基线** + v1.10.3 docling 吞吐与质量优化（ThreadedPdfPipelineOptions 页批处理跑满 GPU、RapidOCR、置信度门控 OCR 重试、页面图片导出支持 ColPali/CLIP 多模态 RAG、bake bge-m3 tokenizer 让 HybridChunker 离线生效）+ **v1.10.4 per-dataset native lance scan opt-in + D-state 熔断器（无向量大数据集经 Rust 聚合下推快 34–145×，熔断器在重复 D-state 时自动降回 pyarrow 冷却）、OLAP 结果分页 + 字段分布统计明细、多语句 SQL 防护、structlog 级别过滤 + gravitino 同步周期日志降噪**。完整历史与路线图方向（更深入的多模态、分布式扩展、更多抽取后端）见 [CHANGELOG](CHANGELOG.md)。

- **6,100+ 测试**，90%+ 覆盖率，零高危安全发现（bandit）
- **186 条 REST 路由**，横跨 22 个 router
- 关键依赖：LanceDB 0.36.0 · DuckDB 1.5.5 · Daft 0.7.21 · Gravitino 1.3.0 · HugeGraph · Docling
- 主干开发（trunk-based），频繁发布

## 社区

- 💬 [Issues / 问答](https://gitee.com/wits__sunpw/wits-infra-dintellihub/issues)
- 🤝 [贡献指南](CONTRIBUTING.md) · [行为准则](CODE_OF_CONDUCT.md)
- 💼 **商业支持 / 咨询 / 定制集成**欢迎洽谈 —— 通过 Issues 联系。

### 👥 维护者

- **Witshine**（[@Witshine](https://github.com/Witshine)）—— 架构、核心引擎，以及在真实企业数据平台上的实战验证（1 亿+ 行分析、大文档解析、GraphRAG 知识平台）。

Arrow Lake 源自生产需求，而非演示玩具。非常欢迎贡献 —— 代码、文档、领域模板、bug 报告；非平凡改动请先开 issue。

非常欢迎贡献（代码、文档、模板、bug 报告）。非平凡改动请先开 issue。

## 许可证

[Apache License 2.0](LICENSE) — © 2026 Witshine.

Apache-2.0 允许你自由地使用、修改与分发 Arrow Lake（含商业用途），只需保留署名与许可声明。为规模化采用而生 —— 也为随之而来的咨询/项目合作而生。
