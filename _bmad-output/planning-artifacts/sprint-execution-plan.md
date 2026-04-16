# Arrow Lake — Sprint 执行计划

## Context

Arrow Lake 规划阶段已完成：8 份核心文档（EN+ZH）同步、80 个 Story 覆盖 68 FRs + 32 NFRs、Implementation Readiness = READY。现在需要将规划转化为可执行的实施计划，为 Sprint 1 启动做好准备。

**参数：** 2 周 Sprint / 2-3 人小团队 / 全部 4 个阶段

---

## Sprint 总览

| Sprint | 时间 | 阶段 | Epic | Stories | 核心里程碑 |
|--------|------|------|------|---------|-----------|
| **1** | Week 1-2 | MVP Core | 1 | 10 | 1.2 Tech Spike NO-GO 通过 + 1.10 Platform Boot |
| **2** | Week 3-4 | MVP Core | 2 + 3(部分) | 9 | 2.6 Schema Migration + 3.1 S3 摄入 |
| **3** | Week 5-6 | MVP Core | 3(完) + 4(部分) | 10 | 3.5 统一多模态表 + 4.1 Text Embedding |
| **4** | Week 7-8 | MVP Core | 4(核心) + 5(部分) | 10 | **5.1 Vector Search — MVP Core Gate** |
| **5** | Week 9-10 | Enhanced | 5(完) + 4(质量) + 6(部分) | 16 | 5.8 性能基准 + G5 管线 dry-run + 6.1-6.4 Metaflow 基础 |
| **6** | Week 11-12 | Enhanced | 6(完) + 7(部分) | 9 | **6.10 Maya E2E — MVP Enhanced Gate** |
| **7** | Week 13-14 | Production | 7 | 7 | 7.3 Argo + 7.10 Helm Chart |
| **8** | Week 15-16 | Production | 7(完) + 8(部分) | 5 | 7.13 分布式扩展测试 |
| **9** | Week 17-18 | Scale | 8 | 4 | 8.5 NeMo Curator GPU（+ buffer） |

**总计：9 个 Sprint（18 周 / ~4.5 个月），80 个 Story，含 2 周 buffer**

---

## 关键路径

```
1.1 → 1.2(NO-GO) → 1.7 → 3.1 → 4.1 → 4.6 → 5.1 → 6.10 → 7.3 → 7.13
```

12 个 Story 串行依赖，决定项目最短工期。

---

## Gate 里程碑

| Gate | Sprint | Story | 通过标准 |
|------|--------|-------|----------|
| **G0: 技术可行性** | Sprint 1 Day 3 | 1.2 | 5 个 NO-GO 触发器全部通过 |
| **G1: 平台就绪** | Sprint 1 Day 10 | 1.10 | `docker compose up` 全服务健康，TTV < 30min |
| **G2: 数据就绪** | Sprint 2 | 2.6 + 3.1 | Schema 迁移 + S3 摄入 50K rows < 1s |
| **G3: 多模态就绪** | Sprint 3 | 3.5 + 4.1 | 统一表 text+image+video + Text Embedding |
| **G4: MVP Core** | Sprint 4 | 5.1 | **Raj can search with embeddings** < 100ms |
| **G5: 管线就绪** | Sprint 5 | 6.1-6.4 | Metaflow `flow.py run` 本地 + 集群 + Maya E2E dry-run（简化版 2 步管线） |
| **G6: MVP Enhanced** | Sprint 6 | 6.10 | **Maya E2E: 1000 records, 4 steps, < 45min** |
| **G7: 生产部署** | Sprint 7 | 7.10 | `helm install` K8s 部署成功 |
| **G8: 规模验证** | Sprint 8 | 7.13 | 单节点 10M rows + 分布式 GPU 弹性 |
| **G9: 全功能** | Sprint 9 | 8.5 | NeMo Curator GPU 质量评分 |

---

## 并行策略（3 人团队）

| 角色 | 职责 | Sprint 分配 |
|------|------|-------------|
| **Dev-A** (平台) | 基础设施、存储、目录、质量 | Epic 1→2→4(质量)→6(编排) |
| **Dev-B** (数据管线) | 摄入、嵌入、搜索 | Epic 3→4(嵌入)→5 |
| **Dev-C** (DevOps) | 生产、可观测性、部署 | Epic 7→8，Sprint 1 协助 1.4/1.5/1.9 |

Sprint 2 起可充分并行：Dev-A 做 Epic 2（版本管理），Dev-B 做 Epic 3（摄入），互不依赖。

---

## 各 Sprint 详情

### Sprint 1: 平台基础 (Week 1-2)

**目标：** `docker compose up -d` 启动全部服务，SDK 可导入，Tech Spike 确认可行性，CI gate 就绪。

| Story | Owner | 工时 | 依赖 |
|-------|-------|------|------|
| 1.1 项目骨架 + 基础 CI | A | 2d | 无 |
| **1.2 Tech Spike** | **A** | **3d** | **无 — HARD GATE（含 5 个 NO-GO 触发器）** |
| 1.3 配置层 | B | 1.5d | 无 |
| 1.4 SDK 基础 | C | 1d | 无 |
| 1.5 可观测性 | C | 2d | 无 |
| 1.6 DuckDB 连接池 | A | 2d | 1.2 通过后 |
| 1.7 Lance 存储 | B | 2d | 1.2 通过后 |
| 1.8 Catalog Actor | B | 2.5d | 1.7 |
| 1.9 Docker Compose | C | 1.5d | 1.5(metrics) |
| 1.10 平台冒烟测试 | A | 0.5d | 1.1-1.9 全部 |

**交付物：** pyproject.toml + uv.lock / `from arrow_lake import Lake` / docs/tech-compatibility.md / /metrics 端点 / GitHub Actions CI（lint + type-check + pytest CPU）/ 2 个零拷贝边界测试（lance_daft, daft_duckdb）

> **注：** Story 1.1 已扩展，包含基础 CI gate（Ruff + MyPy + pytest CPU），从 Story 7.14 拆分。高级 CI（GPU 测试、Helm 验证）保留在 Story 7.14。

---

### Sprint 2: 版本管理 + 摄入启动 (Week 3-4)

**目标：** Maya 可版本化数据集、时间旅行、从本地和 S3 摄入结构化数据。

| Story | Owner | 依赖 |
|-------|-------|------|
| 2.1 自动版本 | A | 1.7 |
| 2.2 命名标签 | A | 2.1 |
| 2.3 时间旅行 | A | 2.1 |
| 2.4 版本比较 | A | 2.1, 2.3 |
| 2.5 Compaction | A | 1.7 |
| 2.6 Schema 迁移 | A | 1.7 |
| 2.7 数据测试框架 | B | 无 |
| 2.8 数据集生命周期 | A | 1.8 |
| 3.1 本地/S3 摄入 | B | 1.7, 1.9 |

**交付物：** create_tag/diff/compact/alter_columns 可用 / 50K CSV 摄入 < 1s / pytest 数据断言工具 / 种子数据集 `data/seed/users.parquet` + `data/seed/documents.jsonl`

---

### Sprint 3: 摄入完成 + 嵌入启动 (Week 5-6)

**目标：** 多模态摄入（文本+图像+视频）全部就绪，Raj 可计算本地 Text Embedding。

| Story | Owner | 依赖 |
|-------|-------|------|
| 3.2 HTTP 摄入 | B | 3.1 |
| 3.3 图像摄入 | B | 3.1 |
| 3.4 视频关键帧 | B | 3.1 (Complex) |
| 3.5 统一多模态表 | A | 2.6, 3.1-3.4 |
| 3.6 多保真度 Blob | A | 3.3, 3.5 |
| 3.7 Daft DataFrame API | C | 3.5 |
| 3.8 Lazy Download | A | 3.6, 3.7 |
| 3.9 元数据搜索桥接 | B | 3.1 |
| 4.1 Text Embedding HF | C | 3.1 |
| 4.3 External API Embedding | C | 无（并行） |

**交付物：** JPEG/PNG/WebP 摄入 + 缩略图 / 视频场景检测 / 统一 Lance 表 / Lazy 加载 / 384-dim Text Embedding

> **注：** Sprint 3 有 10 stories 但并行度高（Dev-A: 3.5/3.6/3.8，Dev-B: 3.2/3.3/3.4/3.9，Dev-C: 3.7/4.1/4.3），3 人团队可充分并行。如 3.8 进度滞后可移至 Sprint 4。交付物含 1 个零拷贝边界测试（duckdb_pytorch）。

---

### Sprint 4: 嵌入完成 + 语义搜索 (Week 7-8) ⭐ MVP Core Gate

**目标：** Raj 可进行向量搜索、全文搜索和混合搜索。全部 Embedding 后端就绪。

| Story | Owner | 依赖 |
|-------|-------|------|
| 4.2 Ray Serve Embedding | C | 4.1 |
| 4.4 Image Embedding | B | 3.3 |
| 4.5 GPU/CPU 异构 | C | 4.1, 4.4 |
| 4.6 异步向量索引 | B | 4.1, 4.4 (**关键路径**) |
| 4.7 内容去重 | B | 3.3, 3.5 |
| 4.8 QualityFilter 注册 | A | 2.7 (**不可延迟 — 6.10 E2E 依赖**) |
| 4.9 内置质量过滤器 | A | 4.8 |
| 4.10 Dead-Letter 持久化 | A | 4.8, 4.9 |
| **5.1 向量搜索** | **B** | **4.6 — MVP Core Gate** |
| 5.2 全文搜索 | C | 3.5 |

**交付物：** Ray Serve + 外部 API Embedding / CLIP Image Embedding / 质量过滤+Dead-Letter / 6 个零拷贝边界测试全部通过 / **Raj 搜索 "autonomous driving safety" 返回 top-10 结果 < 100ms**

> **注：** 4.11/4.12/4.13（质量统计报告、Schema 验证门控、质量分数列）已移至 Sprint 5，以降低 Sprint 4 负载。

---

### Sprint 5: 搜索完成 + 管线编排 (Week 9-10)

**目标：** Raj 可混合搜索+OLAP 分析。Maya 可定义 Metaflow 管线并执行。质量子系统集成完成。

| Story | Owner | 依赖 |
|-------|-------|------|
| 5.3 混合搜索 RRF | B | 5.1, 5.2 |
| 5.4 OLAP 分析 | C | 3.7 |
| 5.5 流式结果 | B | 5.1, 5.4 |
| 5.6 自适应索引 | C | 5.1 |
| 5.7 Catalog SQL 路由 | A | 1.8, 5.1, 5.2 |
| 5.8 性能基准套件 | B | 5.1, 5.4, 5.5 |
| 5.9 数据导出 | C | 3.7 |
| 4.11 质量统计报告 | A | 4.8-4.10 |
| 4.12 Schema 验证门控 | A | 2.6 |
| 4.13 质量分数列 | A | 4.1, 4.4, 4.9 |
| 6.1 Metaflow FlowSpec | A | 1.1 |
| 6.2 集群执行 | A | 6.1 |
| 6.3 瞬态重试 | A | 6.1 |
| 6.4 错误分类 | A | 6.3 |
| 6.6 定时管线 | C | 6.1 |
| 6.7 Tag 追踪 | C | 6.1 |

**交付物：** 混合搜索 alpha 调参 / Daft SQL OLAP / 流式 < 100MB / `python flow.py run` + `--with ray` / 性能基准报告 / 质量统计 + Schema 门控 + 质量分数列

---

### Sprint 6: 管线集成 + 生产启动 (Week 11-12) ⭐ MVP Enhanced Gate

**目标：** Maya 端到端 4 步管线 < 45min。Sam 开始生产部署准备。

| Story | Owner | 依赖 |
|-------|-------|------|
| 6.5 状态回滚 | A | 2.1, 2.3, 6.4 |
| 6.8 分布式处理 | A | 6.2 |
| 6.9 远程数据加载 | B | 4.5, 6.8 |
| **6.10 Maya E2E** | **B** | **3.1, 4.1, 4.8, 5.1 — MVP Enhanced Gate** |
| 6.11 Catalog 读副本 | C | 1.8 |
| 6.12 轻量生产包 | C | 1.9 |
| 7.1 Jupyter 集成 | C | 无 |
| 7.2 CLI | C | 无 |
| 7.14 CI/CD | C | 无 |

**交付物：** Maya E2E Demo / 状态回滚 / 分布式 foreach / CLI / Jupyter / CI Pipeline

---

### Sprint 7: 生产核心 (Week 13-14)

**目标：** Sam 可 Helm 部署 K8s，Prometheus+Grafana 监控，GPU 弹性伸缩。

| Story | Owner | 依赖 |
|-------|-------|------|
| 7.3 Argo Workflows | C | 6.1 |
| 7.4 CronWorkflow | C | 7.3 |
| 7.5 弹性 GPU 突增 | A | GPU infra |
| 7.8 Prometheus Metrics | A | Epics 3-6 |
| 7.9 Grafana Dashboard | B | 7.8 |
| 7.10 K8s Helm Chart | C | 无 |
| 7.11 Docker 网络安全 | B | 无 |

**交付物：** `helm install arrow-lake` / Argo CRD / Cron 调度 / GPU 弹性 < 5min / 17 指标 Grafana 面板

---

### Sprint 8: 生产完成 + 规模启动 (Week 15-16)

**目标：** SQL 查询、Blob 生命周期、单节点+分布式规模测试全部通过。

| Story | Owner | 依赖 |
|-------|-------|------|
| 7.6 SQL Query | B | 无 |
| 7.7 Blob 生命周期 | B | 无 |
| 7.12 单节点测试 | A | 无 |
| 7.13 分布式测试 | A | 7.5 |
| 8.1 分面搜索 | C | Epic 5 |

**交付物：** 10M rows 单节点 / 100 QPS 分布式 / S3 自动分层 / 分面搜索

---

### Sprint 9: 规模阶段 (Week 17-18) + Buffer

**目标：** 全部高级功能交付。

| Story | Owner | 依赖 |
|-------|-------|------|
| 8.2 多模型集成搜索 | B | 4.1, 4.4, 5.1 |
| 8.3 数据血缘 | A | Catalog |
| 8.4 事件溯源审计 | A | Lance version + Metaflow tags |
| 8.5 NeMo Curator GPU | C | 4.8-4.9 |

**交付物：** 多模型 recall 提升 / SQL 数据血缘 / HMAC 审计链 / NeMo GPU 质量评分（或 CPU 回退）

---

## Arrow 零拷贝边界测试分配

project-context.md 定义了 6 个跨组件边界测试。每个测试绑定到对应组件的首个交付 Sprint：

| 边界测试 | 绑定 Story | Sprint | 验证内容 |
|---------|-----------|--------|---------|
| `test_boundary_lance_daft` | 1.7 Lance 存储 | 1 | Lance RecordBatch → Daft DataFrame 共享 Arrow buffer |
| `test_boundary_daft_duckdb` | 1.7 Lance 存储 | 1 | Lance RecordBatch → DuckDB query（catalog-only 路径） |
| `test_boundary_duckdb_pytorch` | 4.1 Text Embedding HF | 3 | DuckDB catalog 结果 → PyTorch tensor（catalog-only 路径） |
| `test_boundary_cpu_gpu` | 4.5 GPU/CPU 异构 | 4 | CPU tensor → GPU tensor pin_memory + non_blocking |
| `test_boundary_ray_object_store` | 4.6 异步向量索引 | 4 | Ray Actor 间通过 Object Store 传递 Arrow 零拷贝 |
| `test_boundary_cudf_arrow` | 4.4 Image Embedding | 4 | cuDF → Arrow 零拷贝（GPU/CuPy 场景） |

所有 6 个测试在 Sprint 4 末尾（MVP Core Gate G4 前）必须全部通过。Story 1.5 ArrowCopyDetector 提供可复用断言工具。

---

## Sprint 文档交付物矩阵

每个 Sprint 产出以下文档，确保可复现性和新成员 onboarding：

| Sprint | Quick Start 更新 | Example Notebook | 种子数据集 | Runbook |
|--------|-----------------|-----------------|-----------|---------|
| 1 | `docs/quickstart.md`（首次创建） | `examples/01_platform_boot.ipynb` | — | — |
| 2 | 更新版本管理章节 | `examples/02_versioning.ipynb` | `data/seed/users.parquet`（1K rows） | — |
| 3 | 更新多模态摄入章节 | `examples/03_multimodal_ingest.ipynb` | `data/seed/images/`（100 images） | — |
| 4 | 更新搜索章节 | `examples/04_vector_search.ipynb` | — | — |
| 5 | 更新管线+质量章节 | `examples/05_quality_pipeline.ipynb` | — | — |
| 6 | 更新 E2E 管线章节 | `examples/06_metaflow_pipeline.ipynb` | — | `docs/runbook-maya.md` |
| 7 | 更新 K8s 部署章节 | `examples/07_k8s_deploy.ipynb` | — | `docs/runbook-sam.md` |
| 8-9 | 更新规模章节 | — | — | `docs/runbook-scale.md` |

**Quick Start 模板结构：** `docs/quickstart.md`
```markdown
# Arrow Lake Quick Start
## Prerequisites
## 5-Minute Setup (docker compose up)
## Your First Query
## Next Steps (链接到 Example Notebooks)
```

**种子数据集 Story（作为 Sprint 2 交付物的一部分）：** Dev-B 在完成 Story 3.1（S3 摄入）后，准备一组标准种子数据集用于后续所有 Sprint 的开发和演示：
- `data/seed/users.parquet` — 1K 行结构化用户数据（Sprint 2）
- `data/seed/documents.jsonl` — 500 条文本文档（Sprint 2）
- `data/seed/images/` — 100 张示例图片（Sprint 3，Story 3.3 交付物）
- 种子数据集通过 CI 自动验证可摄入性

---

## 风险登记

| # | 风险 | Sprint | 严重度 | 缓解 |
|---|------|--------|--------|------|
| R1 | 1.2 Tech Spike NO-GO | 1 | 致命 | 回退方案已文档化；1.3-1.6 可先开始 |
| R2 | Sprint 5 过载 (16 stories, 质量子系统+编排并行) | 5 | 高 | 4.11/4.12/4.13 优先级低于搜索和编排；必要时推迟到 Sprint 6 |
| R3 | 6.10 Maya E2E 集成失败 | 6 | 高 | Day 1 启动、每日冒烟集成测试 |
| R4 | NeMo Curator CPU 回退 | 9 | 中 | 预算 1 额外天；必要时 Sprint 8 启动 |
| R5 | Metaflow + Ray `--with ray` 不兼容 | 1→5 | 中 | Story 1.2 NO-GO #5 提前验证；6.1 再次确认；回退到 `@ray.remote` |

---

## Sprint 仪式

| 时间 | 活动 | 时长 |
|------|------|------|
| Day 1 | Sprint Planning | 1h |
| Day 5 | Mid-Sprint Checkpoint | 30min |
| Day 10 AM | Code Freeze | — |
| Day 10 PM | Sprint Review + Demo | 1h |
| Day 10 PM | Retro | 30min |

---

## 关键文件

- `_bmad-output/planning-artifacts/epics.md` — 80 Story 的唯一来源（AC、依赖、NO-GO）
- `_bmad-output/project-context.md` — 45 条实施规则，所有开发者必须遵循
- `_bmad-output/planning-artifacts/architecture.md` — ADR 决策记录
- `_bmad-output/planning-artifacts/system_design.md` — 组件设计规格

## 验证方式

Sprint 1 完成后：
1. `docker compose up -d` 启动成功
2. `python -c "from arrow_lake import Lake; print('OK')"` 输出 OK
3. `docs/tech-compatibility.md` 存在且包含精确版本 pin
4. `pytest tests/` 通过（至少 Story 1.1-1.6 的单元测试）
