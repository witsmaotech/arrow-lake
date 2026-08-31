# 总览 —— Arrow Lake 是什么？

> 从这里开始。在跑任何代码之前，先用 5 分钟建立对平台的心智模型。

## 一句话定位

Arrow Lake 是一个**生产级多模态数据湖仓**：向量检索、全文检索、SQL 分析、知识图谱与 RAG 引擎 —— 全部在**一个 `Lake` facade** 之后，共享**一份存储层**，由**一套身份/审计平面**治理。无需在五件工具间缝胶水代码。自托管优先（你的数据、你的模型、Apache-2.0）。

## 心智模型：一个 facade，六大支柱

你做的所有事都通过一个 Python 对象：

```python
from arrow_lake import Lake
lake = Lake("./my_lake")   # 本地文件系统 —— 无需 MinIO、无需 Docker 即可开始
```

六个一等支柱共享这一个 facade —— 同一份数据集、同一套身份、同一条审计链路：

| 支柱 | 做什么 | 入口 |
|---|---|---|
| 🗄️ **统一湖仓** | 一份 Lance 存储，容纳向量、文本、图像、结构化字段 | `lake.create_dataset()` |
| 🔎 **混合检索** | 向量 + Tantivy 全文 + RRF 融合（**混合是默认项**） | `lake.search()` / `lake.hybrid_search()` |
| 🕸️ **GraphRAG 与知识图谱** | 数据集级知识图谱 + 带 `relation_type` 富化的 GraphRAG | `lake.kg_build()` / `lake.kg_query()` |
| 💬 **生产级 RAG** | 多 provider、默认混合、重排、抗幻觉、带溯源引用 | `lake.rag_query()` |
| 🖼️ **文档智能** | Docling 解析、多模态嵌入（CLIP）、OCR | `lake.ingest_documents()` |
| 📊 **分析** | DuckDB OLAP + Daft 分布式 DataFrame | `lake.olap_query()` / `lake.daft_query()` |

每一根支柱都是子系统，而非薄薄一层封装。它们都在**同一份 Lance 数据集**上运作 —— 你不必再维护五套客户端与五个鉴权模型。

## 数据流

```
摄入 → 索引 → { 检索 | SQL | RAG | GraphRAG } → 导出 / 治理
```

1. **摄入**多模态数据（CSV / PDF / 图像 / …）到 Lance 数据集。
2. **索引**（向量 IVF_PQ、全文 BM25）—— 多数情况自动完成。
3. 在*同一份*数据集上以四种模式**查询**：语义检索、SQL 分析、RAG 问答、GraphRAG 多跳。
4. 用 RBAC、审计、血缘、脱敏**治理** —— 全部内建。

<p align="center">
  <img src="../architecture-design/diagrams/01-layered-architecture.svg" alt="Arrow Lake 分层架构" width="780">
</p>

## 差异化在哪

- **统一，而非拼装** —— 向量 + 全文 + SQL + 图 + RAG 共享一个 facade、一份存储、一套治理平面。
- **原生 GraphRAG** —— HugeGraph + 模板驱动抽取；在实体/关系密集型问题上，答案比纯向量 RAG 更丰富。
- **真正的多模态** —— 文本、图像、音频、视频、文档；不只是文本嵌入。
- **默认即生产可用** —— RBAC、JWT、审计、Helm chart、可观测性。不是开发玩具。

## 接下来去哪

| 你想…… | 去看 |
|---|---|
| 5 分钟跑通一个能用的例子 | [01 快速入门](./01-quickstart-zh.md) |
| **看整个平台端到端全貌（实战配方）** | [19 REST 实战配方](./19-rest-recipes-zh.md) |
| 摄入你自己的数据 | [02 数据摄取](./02-ingestion-zh.md) |
| 向量 / 全文 / 混合检索 | [04](./04-vector-search-zh.md) · [05](./05-fulltext-search-zh.md) · [06](./06-hybrid-faceted-zh.md) |
| OLAP SQL 分析 | [07 OLAP](./07-olap-analytics-zh.md) |
| RAG 与 GraphRAG | [08 RAG](./08-rag-pipeline-zh.md) · [09 知识图谱](./09-knowledge-graph-zh.md) |
| 配置 / 部署 | [03 配置](./03-configuration-zh.md) · [12 部署](./12-deployment-zh.md) |
| **发布高质量数据集(v1.11.4)** | [20 高质量数据集流水线](./20-hq-dataset-zh.md) |

> **新手？** 最快建立"我懂这个平台能干什么"的路径：[01 快速入门](./01-quickstart-zh.md) → [19 实战配方](./19-rest-recipes-zh.md)（端到端）→ 再进上面任一支柱深入。

权威深度文档（分层、facade、数据流、8 张图）见 [架构文档](../architecture-design/ARCHITECTURE.md)。
