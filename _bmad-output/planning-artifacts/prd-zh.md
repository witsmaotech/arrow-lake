---
stepsCompleted:
  - step-01-init
  - step-02-discovery
  - step-02b-vision
  - step-02c-executive-summary
  - step-03-success
  - step-04-journeys
  - step-05-domain
  - step-06-innovation
  - step-07-project-type
  - step-08-scoping
  - step-09-functional
  - step-10-nonfunctional
  - step-11-polish
  - step-12-complete
inputDocuments:
  - _bmad-output/brainstorming/brainstorming-session-2026-04-10-1500.md
  - _bmad-output/brainstorming/appendix-deep-dives.md
  - docs/superpowers/specs/2026-04-10-multimodal-lakehouse-design.md (git HEAD)
workflowType: 'prd'
project_name: 'wits-infra-dintellihub'
user_name: 'Witshine'
date: '2026-04-11'
documentCounts:
  briefs: 0
  research: 0
  brainstorming: 2
  projectDocs: 1
classification:
  type: greenfield
  domain: scientific_ml_platform
  complexity: medium
language: zh-CN
---

# 产品需求文档 — Arrow Lake

**统一多模态数据湖仓平台**

**作者：** Witshine
**日期：** 2026-04-11
**状态：** 草稿 v1.0

---

## 概要

Arrow Lake 是一个全新构建的**统一多模态数据湖仓平台**，核心技术栈为 **DARMU**（Daft + Argo + Ray + Metaflow + uv），扩展层包含 Lance（存储）、NeMo Curator（质量）和 DuckDB（catalog 元数据存储）。Daft SQL 作为主要 OLAP 引擎执行分析查询。平台提供从数据摄入到检索的端到端基础设施，专为处理文本、图像、视频、音频和结构化数据的 AI/ML 团队设计。

**核心差异化优势在于 Arrow 零拷贝全栈**：Lance → Daft → PyTorch 全链路无序列化开销，端到端性能提升约 4 倍。结合嵌入优先摄入、跨模态统一表、双模查询引擎（OLAP via Daft SQL + 向量 + 全文搜索在同一 SQL 中完成），Arrow Lake 彻底消除困扰当前多模态 ML 平台的数据孤岛问题。

**关键业务成果：**

- 弹性突发处理实现 **90% 成本降低**（Spot GPU + 自动扩缩容 vs 常驻 GPU 集群）
- 5 级懒加载在 1% 选择率下实现 **100 倍查询加速**
- 从笔记本开发到生产 K8s 部署**零代码改动**
- 自动分层 Blob 生命周期管理实现 **56% 存储成本降低**

---

## 1. 愿景与目标

### 1.1 产品愿景

构建基础数据基础设施，使多模态 AI/ML 开发如同处理表格数据一样简单。一个平台中，摄入 100GB 混合模态数据、计算嵌入、执行质量评分和混合语义搜索只需**一条命令**——而非一个数据工程团队。

### 1.2 指导原则

| # | 原则 | 描述 |
| --- | --- | --- |
| 1 | Arrow 原生零拷贝 | 每一层都说 Apache Arrow——无序列化、无拷贝 |
| 2 | 跨模态统一 | 一张表存储所有模态，消除数据孤岛 |
| 3 | 嵌入优先 | 嵌入向量是一等公民，在摄入时计算而非事后补充 |
| 4 | 渐进复杂度 | 简单的事情简单做（1 个函数调用），复杂的事情可以做（完整 K8s） |
| 5 | 自愈默认 | 工作流自动从瞬态故障中恢复，无需人工干预 |

### 1.3 不在范围内（v1）

- 自定义 UI/可视化仪表板（CLI + Notebook 优先）
- 实时流式摄入（批处理优先，v2 增加流式）
- 多用户 RBAC/认证系统（单团队部署，v2 增加多租户隔离）
- 超出 S3/MinIO 的云厂商特定集成
- 模型训练框架（平台提供数据，不提供训练循环）

---

## 2. 成功指标

### 2.1 量化 KPI

| 指标 | MVP 目标 | 生产目标 |
| --- | --- | --- |
| 首次查询时间 | < 5 分钟（本地） | < 10 分钟（集群） |
| 摄入吞吐量（文本，1000 万行） | > 5 万行/秒 | > 20 万行/秒（分布式） |
| 向量搜索延迟（1000 万行） | < 10ms（HNSW） | < 5ms（IVF_PQ + 预过滤） |
| 零拷贝链路利用率 | > 90% Arrow 原生 | > 95% Arrow 原生 |
| 工作流自动恢复率（无需人工） | > 90% | > 95% |
| 存储成本 vs 朴素 Parquet 方案 | < 80%（多精度） | < 60%（自动分层 + 压缩） |
| 开发者上手时间 | < 30 分钟 | < 15 分钟 |

### 2.2 定性指标

- 数据科学家可在单个 Jupyter Notebook 中从原始数据完成混合搜索
- 从本地开发切换到 K8s 集群无需修改任何代码
- 嵌入模型热换无需重写数据或停机
- 质量门禁失败自动回滚到最近已知良好的 Lance 版本

---

## 3. 用户画像与旅程

### 3.1 主要用户画像

**画像 A：ML 数据工程师（Maya）**

- 管理从摄入到模型就绪数据集的数据管线
- 需求：可靠的批处理、质量评分、版本控制、成本可见性
- 痛点：ETL、质量、向量数据库、目录分属不同系统；模态间存在数据孤岛
- Arrow Lake 价值：统一管线（Metaflow）、嵌入优先摄入、Lance 版本管理

**画像 B：应用 ML 科学家（Raj）**

- 实验嵌入、检索增强生成和多模态模型
- 需求：快速迭代、灵活查询、GPU 访问、可复现实验
- 痛点：数据加载慢、GPU 因 CPU 预处理而饥饿、缺乏跨模态搜索
- Arrow Lake 价值：零拷贝 PyTorch DataLoader、远程数据加载器（CPU→GPU）、混合搜索

**画像 C：平台工程师（Sam）**

- 为团队部署和运维平台
- 需求：简单部署、自动扩缩容、成本控制、可观测性
- 痛点：K8s 配置复杂、GPU 成本不可预测、手动扩缩容
- Arrow Lake 价值：Docker Compose 一键启动、弹性突发（$440/月 vs $4,286/月）、自愈工作流

### 3.2 关键用户旅程

**旅程 1：摄入与搜索（Maya — 首次使用）**

1. `docker compose up -d` — 平台启动（MinIO + Ray + Jupyter）
2. 在 Notebook 中编写摄入 Metaflow Flow
3. `python flow.py run` — 摄入数据、计算嵌入、构建向量索引
4. 运行混合搜索：`lake.search("自动驾驶安全", modality="image", top_k=10)`
5. 总耗时：从零到结果约 30 分钟

**旅程 2：扩展到生产（Sam — 部署）**

1. 同一个 Metaflow Flow，无需代码改动
2. `python flow.py --with ray argo-workflows create` — 部署到 K8s
3. KubeRay 在突发负载时自动扩容 GPU Worker，空闲后自动缩回
4. 通过 Ray Dashboard + Prometheus 监控
5. 成本：弹性突发 $440/月 vs 常驻集群 $4,286/月

**旅程 3：模型迭代（Raj — 实验循环）**

1. 用不同模型添加新嵌入列（Lance 零成本 add_column）
2. 在新列上构建索引，旧列仍可查询
3. 通过版本对比评估模型质量：Daft SQL FULL OUTER JOIN 或 SDK API 跨版本对比
4. 推广最佳版本：`lance.create_tag("production")`
5. 无数据重写，零停机

---

## 4. 领域模型

### 4.1 核心概念

```text
┌─────────────────────────────────────────────────────────┐
│  Dataset（数据集）                                       │
│  ├── uri: s3://lake/namespace/dataset.lance             │
│  ├── version: int（写入时自动递增）                       │
│  ├── tags: [string]（命名快照）                          │
│  ├── schema: Arrow Schema                               │
│  └── indices: [VectorIndex, FTSIndex]                    │
│                                                         │
│  Catalog（目录）                                         │
│  ├── datasets: [Dataset]                                │
│  ├── metadata: {name → DatasetInfo}                     │
│  └── singleton: Ray Actor (DuckDB)                      │
│                                                         │
│  Pipeline（管线，Metaflow FlowSpec）                     │
│  ├── steps: [Ingest, Quality, Embed, Index, Publish]    │
│  ├── version_tags: [每步对应的 lance_tag]                │
│  └── schedule: @schedule(daily/hourly/cron)             │
│                                                         │
│  Query（查询）                                           │
│  ├── mode: vector | fts | hybrid | olap | streaming     │
│  ├── source: Lance 数据集 URI                            │
│  └── result: Arrow Table | RecordBatchReader            │
└─────────────────────────────────────────────────────────┘
```

### 4.2 实体关系

- 一个 **Dataset** 在单一 Lance 表中包含多模态数据（跨模态统一）
- 一个 **Catalog** 管理多个 Dataset，实现为 Ray Named Actor + 内嵌 DuckDB
- 一条 **Pipeline** 从 Dataset 读取并写入 Dataset，以 Lance 版本作为检查点
- 一个 **Query** 通过 Catalog 操作 Dataset，支持 5 种查询模式

### 4.3 数据模型（Lance Schema）

```python
# 统一多模态表的标准 Arrow Schema
schema = pa.schema([
    # 身份标识
    pa.field("id", pa.string()),
    pa.field("modality", pa.string()),          # 'text' | 'image' | 'video' | 'audio'
    pa.field("source", pa.string()),
    pa.field("created_at", pa.timestamp("us")),

    # 模态特定列（NULL-safe）
    pa.field("text_content", pa.string()),       # 非文本为 NULL
    pa.field("image_data", pa.binary()),         # Blob 离线存储
    pa.field("video_data", pa.binary()),         # Blob 离线存储
    pa.field("audio_data", pa.binary()),         # Blob 离线存储

    # 质量评分（NeMo Curator）
    pa.field("quality_score", pa.float32()),     # 0.0-1.0 综合质量
    pa.field("is_duplicate", pa.bool_()),
    pa.field("dedup_hash", pa.binary()),
    pa.field("nsfw_score", pa.float32()),
    pa.field("aesthetic_score", pa.float32()),   # 图像美学质量

    # 嵌入向量（多模型）
    pa.field("emb_text_768", pa.list_(pa.float32(), 768)),
    pa.field("emb_clip_512", pa.list_(pa.float32(), 512)),
    pa.field("emb_multimodal_1024", pa.list_(pa.float32(), 1024)),

    # 摘要
    pa.field("caption", pa.string()),
    pa.field("thumbnail", pa.binary()),          # 64x64 或 256x256 预览
])
```

---

## 5. 创新亮点

### 5.1 Arrow 零拷贝全栈

从磁盘到 GPU 的完整数据路径全程使用 Apache Arrow，无任何拷贝或序列化步骤：

| 阶段 | 传统方式 | Arrow 零拷贝 | 加速比 |
| --- | --- | --- | --- |
| Lance → 内存 | Parquet 解压 + 拷贝 | Lance mmap + Arrow | ~2x |
| Daft → DuckDB | to_pandas() → DuckDB | to_arrow() → duckdb.arrow() | ~10x（仅 catalog 查询路径） |
| DuckDB → PyTorch | .df().values → torch.tensor | .arrow() → ArrowDataset | ~5x（仅 catalog 查询路径） |
| CPU → GPU | numpy → torch → .cuda() | Arrow → pin_memory → .cuda(non_blocking) | ~3x |

### 5.2 五级懒加载

| 级别 | 机制 | 示例 |
| --- | --- | --- |
| 1 | Daft 惰性求值 | `df.where(...)` — 直到 `.collect()` 才计算 |
| 2 | Lance 谓词下推 | 过滤下推到 Fragment 扫描——跳过整个文件 |
| 3 | Daft 惰性下载 | `read_images()` — 直到需要解码才下载 |
| 4 | Blob 离线加载 | `SELECT id, caption` — 零 Blob I/O |
| 5 | Daft SQL 下推 | `SELECT count(*)` — 存储层聚合（DuckDB 作为 catalog 查询回退） |

效果：**1% 选择率下 100 倍加速**（从 1000 万行中过滤 10 万行）

### 5.3 嵌入优先摄入

嵌入向量作为摄入管线的不可分割部分进行计算，而非单独步骤。模型热换零成本：重命名旧列 → 添加新列 → 构建索引。旧数据永远不需要重写。

### 5.4 双模查询引擎

通过统一的 QueryEngine（Daft SQL 主路径，DuckDB catalog 桥接）提供五种 SQL 查询模式：

1. **纯向量搜索** — `lance_vector_search(emb_col, query_vec, top_k)`
2. **纯全文搜索** — `lance_fts(text_col, query_text, top_k)`
3. **混合搜索** — `lance_hybrid_search(emb, text, vec, txt, alpha, top_k)`
4. **OLAP 分析** — `SELECT ... GROUP BY ...`（Lance 谓词下推）
5. **组合分析 + 向量** — 聚合结果与向量搜索结果 JOIN

---

## 6. 功能需求

### 6.1 数据摄入

| ID | 需求 | 优先级 |
| --- | --- | --- |
| F-ING-01 | 从本地 FS、S3/MinIO、HTTP 摄入文本/CSV/JSON/Parquet | P0 |
| F-ING-02 | 摄入图像（JPEG/PNG/WebP）并自动生成缩略图 | P0 |
| F-ING-03 | 摄入视频: 提取场景边界关键帧(PyAV)，存为 Lance 图像列 + 时间戳。MVP 范围: 每场景单个关键帧。 | P1 |
| F-ING-04 | 摄入时计算文本嵌入（HuggingFace 本地 / Ray Serve / 外部 API） | P0 |
| F-ING-05 | 摄入时计算图像嵌入（CLIP/SigLIP） | P0 |
| F-ING-06 | 原始数据 + 嵌入存储在统一 Lance 表中 | P0 |
| F-ING-07 | 嵌入完成后异步构建向量索引 | P0 |
| F-ING-08 | 内容寻址去重（SHA-256 精确 + pHash 感知） | P0 | ⬆️ ADR-02 升级
| F-ING-09 | 多精度存储（缩略图 + 预览 + 原图） | P1 |

### 6.2 数据处理

| ID | 需求 | 优先级 |
| --- | --- | --- |
| F-PROC-01 | Daft DataFrame API 用于多模态转换 | P0 |
| F-PROC-02 | GPU/CPU 异构调度（`use_gpu=True`） | P0 |
| F-PROC-03 | SQL 查询支持（Daft SQL + DuckDB） | P1 | ⬇️ ADR-02 降级
| F-PROC-04 | 质量评分管线（NeMo Curator：去重、分类器、美学评分） | P1 |
| F-PROC-05 | 质量评分作为 Lance 列并支持谓词下推 | P0 |
| F-PROC-06 | 图像/视频惰性下载 + 解码（无需全文件下载） | P0 |
| F-PROC-07 | Schema 迁移：添加/修改/删除列无需全量重写 | P0 |
| F-PROC-08 | 通过 Ray 分布式处理（foreach + AutoScale） | P0 | ⬆️ ADR-02 升级
| F-PROC-09 | 远程数据加载器模式（CPU 解码 → Object Store → GPU 训练） | P1 |

### 6.3 存储与版本管理

| ID | 需求 | 优先级 |
| --- | --- | --- |
| F-STOR-01 | Lance 格式存储所有数据，Arrow 原生 I/O | P0 |
| F-STOR-02 | 每次写入自动版本化（Lance version） | P0 |
| F-STOR-03 | 命名标签标记重要版本（实验快照、生产版本） | P0 |
| F-STOR-04 | 时间旅行查询：读取任意历史版本 | P0 |
| F-STOR-05 | 版本对比：比较两个版本（Schema + 行 + 列变更） | P1 |
| F-STOR-06 | 压实合并：合并 Fragment 文件，回收已删除列空间 | P0 | ⬆️ ADR-02 升级
| F-STOR-07 | 自动分层 Blob 生命周期（Standard → IA → Glacier） | P2 |
| F-STOR-08 | S3/MinIO 后端，可配置端点 | P0 |

### 6.4 查询与检索

| ID | 需求 | 优先级 |
| --- | --- | --- |
| F-QRY-01 | 向量搜索（<100 万行用 HNSW，100 万+用 IVF_PQ） | P0 |
| F-QRY-02 | 全文搜索（Lance FTS） | P0 |
| F-QRY-03 | 混合搜索（向量 + 文本，可配置 alpha 权重） | P0 |
| F-QRY-04 | OLAP 分析（Daft SQL 主路径 + Lance 谓词下推，DuckDB catalog 查询回退） | P0 |
| F-QRY-05 | 流式结果（fetch_record_batch_reader，常量内存） | P0 |
| F-QRY-06 | 分面搜索（DuckDB CUBE + 向量搜索） | P2 |
| F-QRY-07 | 自适应索引选择（基于数据规模和查询模式） | P0 | ⬆️ ADR-02 升级（1000 万行需 IVF_PQ）
| F-QRY-08 | 多模型集成搜索（JOIN 多个嵌入列的结果） | P2 |

### 6.5 目录与元数据

| ID | 需求 | 优先级 |
| --- | --- | --- |
| F-CAT-01 | 集中式目录作为 Ray Named Actor（内嵌 DuckDB） | P0 |
| F-CAT-02 | 注册数据集的 Schema、列元数据和统计信息 | P0 |
| F-CAT-03 | 通过 SQL 查询目录元数据 | P0 |
| F-CAT-04 | 通过目录路由的统一搜索 API | P0 |
| F-CAT-05 | 数据血缘作为 SQL 查询（Lance 事件日志） | P2 |

### 6.6 工作流编排

| ID | 需求 | 优先级 |
| --- | --- | --- |
| F-ORCH-01 | Metaflow FlowSpec 编排所有批处理管线 | P0 |
| F-ORCH-02 | 本地执行：`python flow.py run` | P0 |
| F-ORCH-03 | 集群执行：`python flow.py run --with ray` | P0 |
| F-ORCH-04 | 生产部署：`python flow.py --with ray argo-workflows create` | P1 |
| F-ORCH-05a | 瞬态重试：@retry 指数退避，处理 Spot 抢占和网络错误 | P0 |
| F-ORCH-05b | 错误分类：@catch 处理器区分可重试与致命错误 | P0 |
| F-ORCH-05c | 状态回滚：致命错误时 Lance 版本回退到最近已知良好版本 | P0 |
| F-ORCH-06 | 定时管线：@schedule(daily/hourly/cron) | P0 | ⬆️ ADR-02 升级
| F-ORCH-07 | 基于标签的运行追踪和恢复 | P1 | ⬇️ ADR-02 降级
| F-ORCH-08 | 弹性突发：按需自动扩容 GPU Worker，空闲后自动缩回 | P1 |
| F-ORCH-09 | 事件溯源：Lance 版本 + Metaflow 标签 = 不可变审计日志 | P2 |

### 6.7 开发者体验

| ID | 需求 | 优先级 |
| --- | --- | --- |
| F-DEV-01 | 一键启动平台：`docker compose up -d` | P1 | ⬇️ ADR-02 降级
| F-DEV-02 | Jupyter Notebook 集成用于探索性分析 | P1 | ⬇️ ADR-02 降级
| F-DEV-03 | uv 依赖管理（替代 Poetry） | P0 |
| F-DEV-04 | Python SDK：`from arrow_lake import Lake` | P0 |
| F-DEV-05 | 数据测试：对 Lance/Daft/DuckDB 结果的 pytest 断言 | P1 |
| F-DEV-06 | 渐进复杂度：5 级 API（函数 → Daft → SQL → Ray → Metaflow） | P0 |
| F-DEV-07 | CLI 常用操作（ingest、search、status、version） | P2 |

### 6.8 质量管理（架构 ADR-02 派生）

> 以下功能需求在架构设计阶段（ADR-02）派生，以填补质量控制和可观测性的结构性空白。完整规格见 `_bmad-output/planning-artifacts/architecture.md`。

| ID | 需求 | 优先级 |
| --- | --- | --- |
| F-QUA-01 | QualityFilter 注册：可插拔行级过滤接口 | P0 |
| F-QUA-02 | 内置过滤器：TextLengthFilter + ImageResolutionFilter | P0 |
| F-QUA-03 | 死信持久化：拒绝行写入 `{table}_dead_letter` Lance 表 | P0 |
| F-QUA-04 | 质量统计报告：总数/通过/拒绝 + 每过滤器细分 | P0 |
| F-QUA-05 | Schema 验证门禁：严格模式拒绝未知列/类型不匹配 | P0 |

### 6.9 可观测性（架构 ADR-02 派生）

| ID | 需求 | 优先级 |
| --- | --- | --- |
| F-OBS-01 | Prometheus `/metrics` HTTP 端点（Prometheus 格式） | P0 |
| F-OBS-02 | 摄入指标：每表行数/字节数/时长/错误数 | P0 |
| F-OBS-03 | 处理指标：嵌入/质量拒绝/活跃任务数 | P0 |
| F-OBS-04 | 查询指标：每 query_type 的计数/延迟/结果数 | P0 |
| F-OBS-05 | 系统指标：Ray Actor 数/表数/运行时间 | P0 |
| F-OBS-06 | 指标可配置：通过环境变量配置端口/路径，支持禁用 | P0 |

---

## 7. 非功能需求

### 7.1 性能

| ID | 需求 | 目标 |
| --- | --- | --- |
| NF-PERF-01 | 向量搜索延迟（1000 万行，top_k=100） | < 10ms |
| NF-PERF-02 | 摄入吞吐量（文本，单节点） | > 5 万行/秒 |
| NF-PERF-03 | 全链路 Arrow 零拷贝利用率 | > 90% |
| NF-PERF-04 | 懒加载在 1% 选择率下的加速比 | > 100x vs 即时求值 |
| NF-PERF-05 | 流式查询内存占用（1 亿行） | < 100MB |
| NF-PERF-06 | PyTorch DataLoader 零拷贝 + 异步 GPU 传输 | pin_memory + non_blocking |

### 7.2 可靠性

| ID | 需求 | 目标 |
| --- | --- | --- |
| NF-REL-01 | 工作流自动恢复率（无需人工干预） | > 90%（MVP），> 95%（生产） |
| NF-REL-02 | 故障时数据完整性（Lance 版本 + Metaflow 检查点） | 零数据丢失 |
| NF-REL-03 | Catalog Actor 可用性 | max_restarts=3，自动恢复 |
| NF-REL-04 | 瞬态故障 MTTR | < 10 分钟 |

### 7.3 可扩展性

| ID | 需求 | 目标 |
| --- | --- | --- |
| NF-SCALE-01 | 单节点数据量支持 | 1000 万行 |
| NF-SCALE-02 | 分布式数据量支持 | 10 亿行 |
| NF-SCALE-03 | 并发查询支持 | 100 QPS（含读副本） |
| NF-SCALE-04 | GPU 扩缩模型 | 分式 GPU（0.5），最多 8 Worker |
| NF-SCALE-05 | 弹性突发：0 到 8 GPU Worker | 扩容时间 < 5 分钟 |

### 7.4 成本效率

| ID | 需求 | 目标 |
| --- | --- | --- |
| NF-COST-01 | 弹性突发月度成本（100GB/月处理量） | < $500/月 |
| NF-COST-02 | 自动分层存储成本降低（100TB） | > 50% vs 全 Standard |
| NF-COST-03 | 突发工作负载 Spot GPU 利用率 | 可用时 > 70% |
| NF-COST-04 | 基线（空闲）平台成本 | < $400/月 |

### 7.5 易用性

| ID | 需求 | 目标 |
| --- | --- | --- |
| NF-USE-01 | 开发者上手时间 | < 30 分钟 |
| NF-USE-02 | 从本地到生产部署的代码改动量 | 零 |
| NF-USE-03 | 嵌入模型热换 | 零数据重写，零停机 |
| NF-USE-04 | API 复杂度级别 | 5 级（简单 → 高级） |

### 7.6 安全

| ID | 需求 | 目标 |
| --- | --- | --- |
| NF-SEC-01 | 密钥管理 | 环境变量 / .env 文件，禁止硬编码 |
| NF-SEC-02 | S3/MinIO 访问控制 | IAM 角色（生产）/ Access Key（开发） |
| NF-SEC-03 | API 边界输入校验 | 摄入时 Schema 验证 |
| NF-SEC-04 | 容器安全 | 官方基础镜像，最小攻击面 |

### 7.7 可观测性

| ID | 需求 | 目标 |
| --- | --- | --- |
| NF-OBS-01 | 管线指标 | Prometheus + Grafana 仪表板 |
| NF-OBS-02 | Ray 集群监控 | Ray Dashboard（内置） |
| NF-OBS-03 | 结构化日志 | JSON 格式，含关联 ID |
| NF-OBS-04 | 数据质量报告 | Metaflow Cards（每步 HTML 报告） |
| NF-OBS-05 | 每次管线运行的成本追踪 | Ray 资源注解 + Prometheus |

---

## 8. 技术栈

### 8.1 核心栈（DARMU）

| 组件 | 技术 | 最低版本 | 角色 |
| --- | --- | --- | --- |
| **D** | Daft | >= 0.7.8 | 多模态 DataFrame 引擎，Rust 内核 |
| **A** | Argo Workflows | >= 3.5 | K8s 工作流引擎 |
| **R** | Ray | >= 2.54.1 | 分布式计算（Data/Serve/Actor/ObjectStore） |
| **M** | Metaflow | >= 2.19.22 | 面向用户的工作流编排 |
| **U** | uv | latest | Python 依赖管理 |

### 8.2 扩展层

| 组件 | 技术 | 角色 |
| --- | --- | --- |
| 存储 | Lance | 多模态格式、向量索引、版本管理 |
| 质量 | NeMo Curator | 数据质量评分、去重、GPU 加速 |
| OLAP 引擎 | Daft SQL | 主要 OLAP 分析引擎，Arrow 原生 SQL（经 CloudKitchens DREAM 栈验证） |
| Catalog | DuckDB | Catalog 元数据存储，元数据查询 SQL 桥接 |
| 推理 | Ray Serve | 模型服务、自动扩缩容、GPU 管理 |

### 8.3 基础设施

| 组件 | 开发环境 | 预发布环境 | 生产环境 |
| --- | --- | --- | --- |
| 对象存储 | MinIO（Docker） | MinIO（SSH） | AWS S3 |
| 编排 | Docker Compose | Ray SSH（3-4 节点） | Kubernetes + KubeRay |
| GPU | 本地 GPU（可选） | Spot GPU（1-2x） | KubeRay GPU 节点 |
| 消息总线 | asyncio.Queue | asyncio.Queue | Redis Streams |
| 监控 | 无（CLI） | Prometheus + Grafana | Prometheus + Grafana |

---

## 9. 架构概览

### 9.1 分层架构

```text
┌─────────────────────────────────────────────────────────────┐
│                    编排层（Orchestration）                    │
│              Metaflow + Argo Workflows                       │
│  @project │ @schedule │ tag/resume │ @retry/@catch           │
├─────────────────────────────────────────────────────────────┤
│                    处理层（Processing）                        │
│              Daft（Rust 内核，多模态）                         │
│  embed │ classify │ Lazy Download │ SQL │ GPU/CPU 异构        │
├──────────┬───────────────────┬──────────────────────────────┤
│ 质量     │     计算           │         推理                   │
│ NeMo     │  Ray Data         │  Ray Serve                    │
│ Curator  │  Checkpoint       │  Autoscale                   │
│          │  AutoScale        │  分式 GPU                     │
├──────────┴───────────────────┴──────────────────────────────┤
│                    存储层（Storage）                          │
│           Lance + S3（开发用 MinIO / 生产用 AWS）              │
│  统一表 │ 多精度 Blob │ 版本/标签 │ 向量索引/FTS │ 自动分层   │
├────────────┬──────────────────┬─────────────────────────────┤
│  查询      │   目录            │    对象存储                   │
│  Daft SQL  │   Ray Actor      │  Ray Object                  │
│  OLAP+向量 │   + DuckDB       │  Store（零拷贝）              │
│  +全文     │   (Catalog-only) │                              │
└────────────┴──────────────────┴─────────────────────────────┘
│  横切关注点：uv │ Config │ 日志 │ 指标 │ 安全                   │
│  Docker Compose（开发）│ KubeRay（生产）│ CI/CD                 │
└─────────────────────────────────────────────────────────────┘
```

### 9.2 数据流

```text
摄入：  原始数据 → Daft 读取 → 嵌入计算(GPU) → 质量评分 → Lance 写入 → 索引构建(异步)
查询：  SQL/Daft → Catalog Actor → Daft SQL OLAP + Lance hybrid_search → Arrow → 结果
处理：  Metaflow → Daft → Ray 分布式 → NeMo Curator → Lance 合并 → 版本标签
训练：  Lance → Daft → Daft SQL 流式 → ArrowDataset → pin_memory → .cuda(non_blocking)
```

### 9.3 关键架构模式

**Catalog-as-Actor（目录即 Actor）：** Ray Named Actor 包装 DuckDB，解决单写瓶颈。读副本用于水平扩展。`max_restarts=3` 保证容错。

**远程数据加载器：** CPU Worker 解码和转换 → Ray Object Store（零拷贝）→ GPU Worker 训练。消除 GPU 饥饿。

**混合事件总线：** 渐进式演进：`asyncio.Queue`（开发）→ `Ray Queue Actor`（多节点）→ `Redis Streams`（生产）。

**自愈工作流：** 三级恢复：`@retry`（瞬态）→ `@catch` + 分类（语义）→ `resume` + Lance 版本回滚（状态）。

---

## 10. MVP 范围与路线图

### 10.1 MVP（第 1-2 月）

> ADR-02 更新：MVP 范围增加质量过滤和可观测性。

- [ ] P1：Lance + Daft + DuckDB 本地集成
- [ ] P2：文本 + 图像统一表
- [ ] P3：HuggingFace 本地模型嵌入（文本 + 图像）
- [ ] A1：DuckDB in Ray Actor（单节点 Catalog 元数据存储）+ Daft SQL for OLAP
- [ ] A2：lance_vector_search + lance_fts + lance_hybrid_search
- [ ] A5：Docker Compose 本地开发环境
- [ ] O2：基础版本对比
- [ ] Q1：质量过滤器链（TextLengthFilter + ImageResolutionFilter + dead_letter）
- [ ] Q2：Prometheus /metrics 端点，至少 17 项核心指标
- [ ] DARMU 栈：uv + Metaflow + Daft + Ray（本地）
- [ ] Python SDK：`from arrow_lake import Lake`
- [ ] 基础元数据搜索（SQL 文件名/日期/模态过滤）
- [ ] 数据集生命周期管理（删除/归档/恢复）
- [ ] 数据导出为标准格式（Parquet、CSV）

**Sprint 计划：** MVP Core（Week 1-6）覆盖 Epic 1-5（~18 FRs）。MVP Enhanced（Week 6-8）覆盖完整 E2E 管线验证。

**MVP 验收标准（ADR-02 调整后）：**
- 时间：< 45 分钟（原 30 分钟——增加质量过滤配置时间）
- 数据：1000 条混合质量真实记录（含噪声文本、低分辨率图像——非干净数据）
- 管线：4 步（摄入 → 质量过滤 → 嵌入 → 搜索），非 3 步
- 验证：TTV + /metrics 端点可观测

### 10.2 生产化（第 3-6 月）

- [ ] 视频支持（Daft Video + Cosmos-Embed1）
- [ ] A3：S3 + KubeRay RayJob 部署
- [ ] A4：Redis Streams 事件总线
- [ ] O1：S3 Lifecycle + Glacier 自动分层
- [ ] O3：NeMo Curator GPU 去重 + 评分管线
- [ ] O4：Metaflow @retry + Ray Checkpoint 自愈（完整三级）
- [ ] 多租户：KubeRay namespace + Lance 路径前缀隔离
- [ ] Argo Workflows 生产部署
- [ ] Prometheus + Grafana 监控
- [ ] Catalog 读副本高可用（只读故障转移）
- [ ] 轻量级生产部署包（docker-compose.prod.yml + 健康检查）

### 10.3 规模化（第 6-12 月）

- [ ] 自适应索引选择（自动 HNSW/IVF_PQ）
- [ ] 多模型集成搜索
- [ ] 分面搜索（DuckDB CUBE）
- [ ] 边缘湖仓（Jetson Orin 部署）
- [ ] 多模态 RAG 管线（Ray Serve 重排）
- [ ] 自演化管线（Metaflow 参数搜索 + 反馈）
- [ ] MotherDuck 云目录集成（若保留 DuckDB）或 Daft SQL 分布式联邦查询

---

## 11. 待决问题与风险

### 11.1 待决问题

| # | 问题 | 影响 | 需要决策的时间 |
| --- | --- | --- | --- |
| 1 | Lance Parquet ↔ Lance 原生转换开销 | 数据迁移复杂度 | 原型验证 |
| 2 | DuckDB Lance 扩展的生产级成熟度 | 查询可靠性 | 持续跟踪上游进展 |
| 3 | Daft + Ray 集成在大规模下的稳定性 | 处理可靠性 | 1 亿+行负载测试 |
| 4 | NeMo Curator Lance 桥（cuDF → Arrow）性能 | 质量管线吞吐量 | 原型验证 |
| 5 | Ray AutoScale v2 Spot 实例抢占行为 | 成本可预测性 | 预发布环境测试 |

### 11.2 技术风险

| 风险 | 概率 | 影响 | 缓解措施 |
| --- | --- | --- | --- |
| Lance 破坏性 API 变更 | 低 | 高 | 锁定版本，升级前测试 |
| DuckDB 单写瓶颈在高负载下 | 中 | 中 | Catalog Actor 读副本 |
| Ray GCS 在大规模下成为瓶颈 | 低 | 高 | 使用 Redis 事件总线做协调 |
| NeMo Curator 仅支持 NVIDIA GPU | 高 | 中 | 回退到 CPU 质量评分 |
| Metaflow Argo 集成问题 | 低 | 中 | 直接使用 Argo YAML 作为兜底 |
