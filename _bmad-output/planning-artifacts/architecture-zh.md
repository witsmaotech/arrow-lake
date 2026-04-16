---
stepsCompleted: [1, 2, 4, 5, 6, 7, 8]
step3Skipped: true
step3Impact: |
  Step 3（组件生态系统）在架构决策阶段被跳过。影响：
  - 依赖版本未全部验证（Daft 版本存在 >=0.4.0 与 >=0.7.0 矛盾）
  - 辅助库版本（structlog, tenacity, pydantic, boto3, prometheus-client）推迟到实现时定义
  - 建议实现前进行"Step 3 Lite"：验证 Daft>=0.7.8 + DuckDB Lance 扩展 + Pydantic v2 Arrow 类型映射
lastStep: 8
status: 'complete'
completedAt: '2026-04-11'
inputDocuments:
  - _bmad-output/planning-artifacts/prd.md
  - _bmad-output/planning-artifacts/prd-zh.md
  - docs/superpowers/specs/2026-04-10-multimodal-lakehouse-design.md (git HEAD)
  - _bmad-output/brainstorming/brainstorming-session-2026-04-10-1500.md
  - _bmad-output/brainstorming/appendix-deep-dives.md
workflowType: 'architecture'
project_name: 'arrow-lake'
user_name: 'Witshine'
date: '2026-04-11'
language: 'zh'
chineseVersionOf: architecture.md
---

# Architecture Decision Document — Arrow Lake

_This document builds collaboratively through step-by-step discovery. Sections are appended as we work through each architectural decision together._

## Project Context Analysis

### Requirements Overview

**Functional Requirements:**

57 条 PRD 功能需求分布在 7 个类别：摄入（9 条）、处理（9 条）、存储（8 条）、查询（8 条）、目录（5 条）、编排（11 条，含 F-ORCH-05 拆分为 05a/05b/05c）、开发者体验（7 条）。P0 需求 39 条，P1 需求 12 条，P2 需求 6 条。经 ADR-02 补充 11 条派生 FR（F-QUA-01~05 质量管控 + F-OBS-01~06 可观测性），合计 68 条。P0 需求总计 50 条，P1 需求 12 条，P2 需求 6 条。

**Non-Functional Requirements:**

27 条非功能需求横跨 7 个领域：性能（6 条，核心约束：向量搜索 <10ms、零拷贝利用率 >90%）、可靠性（4 条，核心约束：自动恢复率 >95%）、可扩展性（5 条，核心约束：弹性扩容 <5 分钟）、成本（4 条，核心约束：月度 < $500）、易用性（4 条，核心约束：上手 <30 分钟）、安全（4 条）、可观测性（5 条）。

**Scale & Complexity:**

- Primary domain: Scientific ML platform / backend infrastructure
- Complexity level: Medium（greenfield, single-team, no RBAC）
- Estimated architectural components: ~15

### Technical Constraints & Dependencies

**核心架构约束（通过 ADR 分析确认）：**

1. **Arrow 零拷贝是铁律** — 所有组件边界必须输出 Arrow 格式。任何组件如果需要拷贝/序列化，那是集成 Bug，不是架构选择
2. **Ray Placement Group 是零拷贝的前提** — CPU/GPU Worker 必须同节点，否则 Object Store 退化为序列化传输（退化 100-500x）
3. **Catalog Actor 只做元数据管理** — DuckDB 嵌入 Catalog Actor 仅负责元数据存储和 Catalog 查询；OLAP 分析由 Daft SQL 执行，>100 QPS 场景需要读副本（Story 6.11）
4. **Lance Fragment 大小必须监控** — 128MB-512MB 是最优范围，写入后自动 compact_files
5. **版本膨胀需要主动管理** — @schedule 定期 cleanup，production tag 永久保留
6. **GPU 成本需要硬性上限** — namespace ResourceQuota + Prometheus 预算告警

**技术依赖矩阵：**

| 依赖 | 版本约束 | 风险 | 降级方案 |
|------|---------|------|---------|
| Lance | >= 4.0.0 | API 变更可能打破零拷贝链 | Pin 版本 + 集成测试 |
| Daft | >= 0.7.8 | Ray 集成稳定性 | 降级到 Daft 单机模式 |
| Ray | >= 2.54.1 | GCS 瓶颈、AutoScale v2 | Redis 事件总线替代 |
| DuckDB | >= 1.5.1 | Lance 扩展成熟度 + WAL 多连接稳定性 | Catalog-only 降级：Daft SQL 接管 OLAP，DuckDB 仅保留元数据存储 |
| Metaflow | >= 2.19.22 | Argo 集成问题 | 直接 Argo YAML |
| NeMo Curator | >= 1.1.0 | 仅 NVIDIA GPU、cuDF→Arrow 桥 | CPU 质量评分回退 |

### Cross-Cutting Concerns Identified

**跨组件关注点：**

1. **Arrow 零拷贝纪律** — 所有数据路径（Lance→Daft, Lance→DuckDB, Lance→PyTorch）必须验证组件边界间的 Arrow 共享内存，不允许中间序列化
2. **配置管理** — Pydantic Settings，按环境（dev/staging/prod）区分，通过 Metaflow Config 注入
3. **结构化日志** — JSON 格式 + correlation ID（Metaflow run_id），跨分布式组件追踪
4. **成本追踪** — Ray 资源注解 + Prometheus，每次管线运行记录 GPU-hours 和成本
5. **Schema 演进兼容** — Lance add_columns（零成本）优于 alter_columns（需重写），新列 nullable

**风险识别（通过事前验尸 + 故障模式分析）：**

| # | 风险 | 概率 | 影响 | 预防措施 |
|---|------|------|------|---------|
| R1 | Arrow 零拷贝链断裂（依赖升级） | 中 | 致命 | Pin 版本 + 零拷贝链回归测试 |
| R2 | Catalog Actor 单点故障（内存泄漏/高 QPS） | 高 | 严重 | 读副本 + 内存监控 + 自动重启 |
| R3 | Lance 版本膨胀（存储成本失控） | 中 | 中 | @schedule 定期 cleanup + 保留策略 |
| R4 | GPU 成本失控（Worker 不释放） | 高 | 高 | shutdownAfterJobFinishes + ResourceQuota |
| R5 | Ray Object Store 跨节点退化 | 中 | 高 | Placement Group 约束同节点 |
| R6 | Spot Worker 高频抢占 | 高 | 低 | AutoScale v2 自动替换 + 重试 |
| R7 | cuDF→Arrow 桥性能瓶颈 | 中 | 中 | 原型验证 + CPU 回退 |
| R8 | DuckDB Lance 扩展 Bug + WAL 多连接失败 | 中 | 高 | 降级到 Daft SQL 接管 OLAP；Story 1.2 Spike 验证（3 天限时，含 NO-GO 触发器） |
| R9 | Arrow Schema 演化不兼容 | 中 | 高 | DuckDB/Daft 对 schema 变更容忍度验证 |

### Architecture Decisions from ADR Analysis

**ADR-01: Catalog 架构 — 连接池型（方案 C）**

经过路由型（A）vs 分离型（B）vs 连接池型（C）三方辩论，裁决采用方案 C。

| 维度 | A: 路由型 | B: 分离型 | C: 连接池型 ✅ |
|------|-----------|-----------|-------------|
| 吞吐上限 | ~50-80 QPS | 高（水平扩展） | ~100-200 QPS |
| 架构复杂度 | 低 | 高 | 中 |
| 数据一致性 | 强 | 最终一致 | 强 |
| 开发成本 | 低 | 高 | 中 |
| 故障隔离 | 单点 | 好 | 半隔离 |

**设计要点：**
- Catalog Actor 保留单例，但内部实现 DuckDB WAL 连接池（4 读连接 + 1 写连接，catalog-only 默认值；原 8 读连接已因 OLAP 迁移至 Daft SQL 而缩减）
- 连接池仅服务于元数据操作（schema 查询、表注册、版本列表）；OLAP 分析由 Daft SQL 执行
- 流式查询通过 Daft SQL 执行（非 DuckDB 连接池）
- 演进路径：Phase 1 catalog-only 连接池 → Phase 2 读副本（Story 6.11）提升高可用

**ADR-02: MVP P0 范围补充 — 质量管控 + 可观测性**

MVP P0 新增 11 条 FR，来自两个结构性缺口的填补：

**质量管控（5 条）：**

| ID | 需求 | 描述 |
|----|------|------|
| F-QUA-01 | 质量过滤器注册 | `QualityFilter` 抽象接口，支持行级过滤器串行执行 |
| F-QUA-02 | 内置基础过滤器 | `TextLengthFilter` + `ImageResolutionFilter` 参考实现 |
| F-QUA-03 | Dead-letter 持久化 | 被拒绝记录写入 `_dead_letter` Lance 表，含拒绝原因 |
| F-QUA-04 | 质量统计报告 | 记录 total/passed/rejected 行数 + 按过滤器维度的拒绝分布 |
| F-QUA-05 | Schema 校验门控 | 摄入时可选严格模式，校验列类型和非空约束 |

**可观测性（6 条）：**

| ID | 需求 | 描述 |
|----|------|------|
| F-OBS-01 | Prometheus 端点 | `/metrics` HTTP 端点，Prometheus 格式 |
| F-OBS-02 | 摄入指标 | 行数/字节数/耗时/错误数，按 table_name 分组 |
| F-OBS-03 | 处理指标 | 嵌入行数/耗时/质量拒绝数/活跃任务数 |
| F-OBS-04 | 查询指标 | 查询次数/延迟/结果数，按 query_type 分组 |
| F-OBS-05 | 系统指标 | Ray Actor 数量/表数量/运行时长 |
| F-OBS-06 | 指标可配置 | 环境变量控制端口/路径，支持禁用 |

**最小 Prometheus 指标集（17 个）：** `arrow_lake_ingestion_rows_total`, `arrow_lake_ingestion_bytes_total`, `arrow_lake_ingestion_duration_seconds`, `arrow_lake_ingestion_errors_total`, `arrow_lake_embedding_rows_total`, `arrow_lake_embedding_duration_seconds`, `arrow_lake_quality_rejected_rows_total`, `arrow_lake_processing_active_tasks`, `arrow_lake_query_total`, `arrow_lake_query_duration_seconds`, `arrow_lake_query_result_count`, `arrow_lake_ray_actors_active`, `arrow_lake_lance_table_count`, `arrow_lake_lance_fragment_size_bytes`, `arrow_lake_ray_gpu_hours_total`, `arrow_lake_lance_version_count`, `arrow_lake_uptime_seconds`.

**MVP Gate 调整：**
- 时间：30 分钟 → 45 分钟（增加质量过滤配置时间）
- 数据：干净数据 → 1000 条混合质量真实数据（含噪声文本、低分辨率图像）
- 管线：三步 → 四步（摄入→质量过滤→嵌入→检索）
- 验证：TTV + /metrics 端点可观测

**ADR-03：Ray Object Store 大小与淘汰策略**

将多 GB 的 Lance Fragment 加载到 Ray Object Store 进行 GPU 处理（远程数据加载器模式，Story 7.5）时，如果没有明确的大小规划，内存压力是不可避免的。

| 维度 | 方案 A：固定预算 | 方案 B：比例分配 | 方案 C：自适应 ✅ |
|------|-----------|-----------|-------------|
| 内存分配 | 每个 worker 固定 2GB | 每个 worker 占节点 RAM 40% | 60% 可用内存（不含 head）+ LRU 淘汰 |
| 磁盘溢出 | 不支持 | 手动触发 | 80% 阈值自动触发 |
| GPU pin_memory | 不管理 | 按批次预分配 | 按 `ArrowDataset` 请求动态分配 |
| 复杂度 | 低 | 中 | 中 |
| 生产风险 | 大批次时 OOM | 资源利用率不足 | Ray 文档中的成熟模式 |

**设计要点：**
- Object Store 内存预算 = 60% 节点可用 RAM（排除 head 节点和系统开销）
- LRU 淘汰在 80% 容量时触发；被淘汰的 Arrow 表按需从 Lance 重新读取（零拷贝保持完整）
- 在 80% 容量时启用磁盘溢出到 `/tmp/ray_spill`，worker 退出时自动清理
- `pin_memory` 由 PyTorch `ArrowDataset` 管理——用户代码中无手动 `cuda` 调用
- 监控指标：`arrow_lake_ray_object_store_usage_bytes`（Gauge）、`arrow_lake_ray_object_store_evictions_total`（Counter）
- 大小规格在 Story 7.5 中使用 10GB 图像数据集验证，之后方可投入生产

**ADR-04：Embedding 模型服务策略**

存在三条 embedding 路径（HuggingFace 本地、Ray Serve、外部 API），但 MVP 缺乏明确的默认选择。明确的默认值可防止分析瘫痪和不可控的 GPU 成本。

| 维度 | 方案 A：HuggingFace 本地 ✅（MVP） | 方案 B：Ray Serve（生产） | 方案 C：外部 API（可选） |
|------|-----------|-----------|-------------|
| 基础设施 | 单 GPU 或 CPU | Ray Serve 集群 | API Key + 网络 |
| 延迟 | ~50-200ms/批次 | ~20-100ms/批次 | ~100-500ms/批次 |
| 成本 | 免费（自托管） | Ray GPU 小时计费 | 按请求计费 |
| 模型热切换 | 重启 flow | 蓝绿部署 | Header 路由 |
| 复杂度 | 低 | 中 | 低 |

**设计要点：**
- **MVP 默认（第 1-6 周）：** HuggingFace `SentenceTransformers` 本地推理——除现有 GPU worker 外零额外基础设施。模型首次加载后缓存，通过 Object Store 中的 `model_cache` 在 Ray 任务间共享。
- **生产规模（第 3 月+）：** 当并发推理 > 10 QPS 或需要多模型服务时迁移到 Ray Serve（Story 8.8）。迁移路径：将现有 `Encoder` 类封装为 Ray Serve deployment——API 无变化。
- **外部 API（可选）：** 通过 `EmbeddingProvider` 接口支持（Story 1.4）。适用于专有模型（OpenAI `text-embedding-3-large`）。通过 `arrow_lake_embedding_external_requests_total` 指标进行速率限制和成本追踪。
- **成本治理：** `shutdownAfterJobFinishes` 确保 GPU worker 在 embedding 批次完成后释放。Metaflow `@resources(gpu=1)` 控制每个 flow 的 GPU 分配。

### Functional Requirement Conflicts Identified

| 冲突 | 涉及需求 | 解决方案 |
|------|---------|---------|
| 零拷贝 vs NeMo Curator | F-PROC-04 + 约束 #1 | NeMo Curator cuDF→Arrow 为受控拷贝点，NF-PERF-03 计算排除此阶段 |
| Catalog 单例 vs 100 QPS | F-CAT-01 + NF-SCALE-03 | 采用 ADR-01 连接池方案，查询直连绕过 Actor 路由 |
| 渐进复杂度 vs 零拷贝 | F-DEV-06 + 约束 #2 | L4 级别提供默认 Placement Group 模板，`ArrowCopyDetector` 检测非 Arrow 传输 |
| 嵌入列膨胀 vs 内存 | F-ING-04/05 + NF-PERF-06 | 80GB 嵌入无法全量 pin_memory，IVF_PQ 压缩从 P2 提升为 P0 前提 |

### MVP First-Week Execution Plan

**"先集成后功能"策略：** 5 天完成端到端零拷贝管道验证。

| Day | 上午 | 下午 |
|-----|------|------|
| 1 | 环境可达性验证 + sample Lance dataset fixture | 边界 1-3：Lance→Daft, Daft→DuckDB, DuckDB→PyTorch |
| 2 | 边界 4：CPU→GPU（pin_memory） | 边界 5-6：Ray Object Store + cuDF→Arrow |
| 3 | 端到端链路 smoke test + 性能基线 | SDK 接口定义（`ArrowLakeClient`） |
| 4 | 最小管线实现：ingest→index→search | 管线集成测试 |
| 5 | Docker Compose + TTV 自动化测试 | CI pipeline 配置 + 基线记录 |

**测试策略分层：**

| 层级 | 覆盖 | MVP 目标 |
|------|------|---------|
| Unit | 每个算子/连接器 | 80% |
| Integration | 6 个 Arrow 边界 | 关键路径 100% |
| E2E | 完整管线（4 步） | 主流程 100% |
| Contract | Arrow Schema 兼容性 | Schema 变更 100% |
| Performance | 搜索延迟/吞吐 | P50 基线对比 |

**零拷贝验证手段：** Arrow Buffer 地址比对（`buf.address`），refcount 检测确认共享内存，非"感觉像零拷贝"而是量化证据。

### Priority Adjustments

**提升到 P0：**
- F-ING-08（内容寻址去重）P1→P0：去重是质量管线前置依赖
- F-PROC-08（Ray 分布式）P1→P0：MVP 路线图已包含 `--with ray`
- F-ORCH-06（@schedule）P1→P0：版本膨胀自动化管理的必要条件
- F-STOR-06（compact）P1→P0：Fragment 大小控制的前提条件
- F-QRY-07（自适应索引）P2→P0：1000 万行下 IVF_PQ 是必要条件

**可推迟到 P1：**
- F-DEV-01（Docker Compose）P0→P1：便利性，不影响核心功能
- F-DEV-02（Jupyter）P0→P1：UX 优化
- F-PROC-03（SQL 查询）P0→P1：Python API 先行，SQL 后补
- F-ORCH-07（标签追踪恢复）P0→P1：Metaflow 自带 run_id 追踪

### KPI Recommendations

**MVP 阶段三个 OMTM（One Metric That Matters）：**

1. **TTV（Time to Value）：** 新用户从 `docker compose up` 到第一条 hybrid search 结果 < 45 分钟
2. **管线完成率：** 用户尝试跑通完整 4 步管线（摄入→质量→嵌入→检索）的成功率 > 70%
3. **周活跃使用天数：** 内测用户每周使用 >= 3 天

替代不精确的主观指标（开发者满意度 NPS、绝对查询延迟），使用行为指标验证产品价值。

### Risk Priority Reassessment

| 优先级 | 风险 | 理由 |
|--------|------|------|
| **P0** | DuckDB Lance 扩展 Bug | 查询层唯一出口，第三方依赖不可控 |
| **P0** | 零拷贝链断裂 | 性能基线，任一环节断裂 = 性能不可用 |
| **P1** | Arrow Schema 演化不兼容 | DuckDB/Daft 对 breaking change 容忍度不同 |
| **P1** | GPU 成本失控 | 商业风险，可能导致项目被叫停 |
| **P1** | Catalog 单点故障 | 连接池方案已缓解，但路由仍是关键路径 |
| **P2** | 版本膨胀 | 有明确 mitigation |
| **P2** | Spot Worker 抢占 | Ray 内置恢复机制 |
| **P3** | Object Store 跨节点退化 | Placement Group 部署拓扑问题 |
| **P3** | cuDF→Arrow 桥瓶颈 | 可通过批处理调优控制 |

### MVP Evolution Path Risks

**峡谷 1：本地 Docker → 多节点 Ray on K8s**
- 数据分片策略未定义
- Ray 集群生命周期管理学习曲线
- **建议：** 插入 Mini Cluster 里程碑（3-4 节点，Ray autoscaler + SSH 模式）

**峡谷 2：技术验证 → 生产上线**
- 无多租户隔离（Raj GPU 任务可能饿死 Maya ETL）
- 无数据治理（血缘/审计/访问控制）
- **建议：** 提前规划多租户架构，Beta→Production 过渡时间可能被低估 2-3 倍

## Core Architectural Decisions

### Decision Priority Analysis

**Critical Decisions (Block Implementation):**

| ID | Decision | Rationale |
|----|----------|-----------|
| D-1.1 | Schema: Pydantic-first | SDK 体验核心，所有数据操作的基础 |
| D-2.1 | SDK: Hybrid（Fluent + Declarative） | 用户交互主入口，API 契约定义 |
| D-2.3 | 错误处理: Exception + tenacity | 跨 Ray Actor 边界的错误传播基础 |
| D-3.1 | 部署: Docker Compose + Helm Chart | TTV < 45min 的前提 |
| D-4.2 | 加密: TLS + EBS + 内存裸访问 | Arrow 零拷贝链的硬约束 |

**Important Decisions (Shape Architecture):**

| ID | Decision | Rationale |
|----|----------|-----------|
| D-1.2 | 索引: 按需构建 + 增量更新 | 向量搜索性能保障 |
| D-1.3 | 索引模式: 每列一索引 | FTS + 向量混合查询的前提 |
| D-2.2 | Actor 通信: 纯 Ray Actor Call | MVP 简单性，Phase 2 演进到 Queue |
| D-3.2 | CI/CD: Squash merge + Trunk-based | 单团队效率最优 |
| D-3.6 | Metaflow: @project + Config YAML | 管线可复现性 |

**Deferred Decisions (Post-MVP):**

| Decision | Rationale | Earliest Phase |
|----------|-----------|---------------|
| 前端 UI | MVP 是纯后端 + Python SDK | Phase 2 |
| API Key / OAuth2 认证 | MVP 无外部用户，Docker 网络隔离足够 | Phase 2 |
| Ray Queue 解耦 | MVP Actor 数量少，直接 RPC 足够 | Phase 2（多租户） |
| Vault / HCSI Secrets | .env 足够 MVP 使用 | Production |
| K8s NetworkPolicy | Docker 默认隔离足够 | Production |

### Data Architecture

**D-1.1 Schema 定义: Pydantic-first**

- 用 Pydantic v2 model 定义业务 Schema，运行时转换为 `pyarrow.schema()`
- 理由：Python SDK 优先的平台，Pydantic 提供类型安全、IDE 补全、JSON 序列化
- Pydantic v2 的 `CoreSchema` → Arrow 类型映射：`str→pa.string()`, `int→pa.int64()`, `float→pa.float32()`, `list[float]→pa.list_(pa.float32())`
- Schema 演进遵循 Lance 规则：`add_columns`（零成本）优于 `alter_columns`（需重写），新列 nullable

**D-1.2 索引构建: 按需触发**

- 用户显式调用 `lake.table("docs").create_index()` 触发索引构建
- MVP 不做 auto-index，Phase 2 考虑 `after_commit` hook 自动构建
- 理由：用户控制更安全，索引构建是 GPU 密集操作需显式确认

**D-1.3 索引模式: 每列一索引**

- 文本列 → FTS 索引（Tantivy via Lance）
- 向量列 → IVF_PQ 索引（Lance 内置）
- 混合查询分别命中不同索引，结果合并
- 理由：FTS 和向量是正交维度，统一索引无法同时优化

**D-1.4 索引更新: 增量更新**

- Lance version append 友好，新数据 append 后增量更新索引
- 避免全量重建（1000 万行全量重建耗时不可接受）

**D-1.5 Ray Object Store 缓存: LRU + TTL(30min)**

- Ray `put/get` 内置 LRU 淘汰
- 叠加 30min TTL 防止长运行管线内存泄漏
- 手动释放接口：`lake.cache.evict(table_name)` 供用户主动清理

**D-1.6 Blob out-of-line 阈值: 1MB**

- 超过 1MB 的列值（如原始图像 bytes）延迟加载
- PyTorch DataLoader 按 batch 需求触发实际读取
- 阈值可通过配置调整

### API & Communication

**D-2.1 SDK 设计: Hybrid 模式**

- **交互式查询（Fluent Builder）：** `lake.table("docs").search("query").vector(top_k=10).to_arrow()`
- **批处理管线（Declarative Config）：**
  ```python
  pipeline = IngestPipeline(
      source=S3Source(bucket="my-data", prefix="images/"),
      filters=[TextLengthFilter(min_chars=10), ImageResolutionFilter(min_px=64)],
      embed=True,
      index=True,
  )
  pipeline.run()
  ```
- 风格参照 Daft 自身 API，降低学习成本

**D-2.2 Actor 通信: 纯 Ray Actor Call**

- MVP 所有内部组件通信通过 `actor.method.remote()` 直接调用
- 无需 Message Queue 解耦
- 理由：Actor 数量有限（~5-10），单 namespace，Ray Actor Call 内置序列化/重试/超时
- 演进路径：Phase 2 多租户场景引入 Ray Queue 做背压和解耦

**D-2.3 错误处理: Custom Exception + tenacity**

- 异常层次：
  ```
  ArrowLakeError (base)
  ├── IngestionError
  │   ├── SourceConnectionError
  │   ├── SchemaValidationError
  │   └── QualityFilterError
  ├── QueryError
  │   ├── IndexNotFoundError
  │   └── QueryTimeoutError
  ├── CatalogError
  │   ├── TableNotFoundError
  │   └── ConnectionPoolExhaustedError
  └── RayRuntimeError
      ├── WorkerUnavailableError
      └── PlacementGroupError
  ```
- 重试策略（tenacity）：
  - Spot Worker 抢占：`retry(stop_after_attempt=3, wait=exponential(multiplier=1, max=30))`
  - 临时性网络错误：`retry(stop_after_attempt=5, wait=exponential(multiplier=0.5, max=10))`
  - 不可重试错误（Schema 验证失败等）：不重试，直接抛出

**D-2.4 REST API: MVP 仅 Python SDK**

- MVP 不提供 HTTP REST 层
- `/metrics` 端点独立暴露（Prometheus scrape），不等于完整 REST API
- 5 级 API 渐进复杂度通过 Python SDK 实现（L1 Function → L5 Metaflow）
- Phase 2 引入 FastAPI 包装供非 Python 客户端

### Infrastructure & Deployment

**D-3.1 部署拓扑: Docker Compose + Helm Chart**

- **开发环境：** `docker compose up` 一键启动
  - Ray head + 1 worker（CPU only，GPU 可选）
  - DuckDB（catalog actor 内嵌）
  - Prometheus + Grafana（监控）
- **生产环境：** Helm Chart 部署 Ray on K8s
  - Ray 官方 Helm Chart + 自定义 values
  - Prometheus Operator + ServiceMonitor
- **演进路径：** Docker Compose → Mini Cluster（3-4 节点 Ray SSH）→ K8s Helm

**D-3.2 CI/CD: Squash merge + Trunk-based**

- 单分支 `master`，功能分支 + PR
- Squash merge 保持主分支线性
- PR 门控：Ruff lint + MyPy type check + pytest（CPU）→ 合并 → GPU nightly + E2E

**D-3.3 GPU 测试: Nightly + 手动触发**

- CPU 测试每次 PR 运行（覆盖逻辑正确性）
- GPU 测试 nightly 自动运行 + PR 评论 `@bot run-gpu` 手动触发
- 理由：GPU runner 成本高，零拷贝边界测试需要真实 GPU

**D-3.4 配置管理: 四层叠加**

```
代码默认值 → .env 文件（本地） → 环境变量（Docker/K8s） → Metaflow Config YAML（运行时）
```

- Pydantic Settings 自动合并四层
- 启动时全量校验，缺失必填项立即报错（Fail Fast）

**D-3.5 Secrets: .env → Vault**

- MVP：`.env` 文件 + `.gitignore` 排除
- 生产：环境变量注入 / HashiCorp Vault（Phase 2）
- `.env.example` 提供模板，不含真实值

**D-3.6 Metaflow 参数注入: @project + Config YAML**

```python
from metaflow import FlowSpec, step, project

@project(name="arrow-lake")
class IngestFlow(FlowSpec):
    @step
    def start(self):
        config = self.config  # 从 Config YAML 注入
```

- 声明式、可版本化、可 diff
- Config YAML 按 environment（dev/staging/prod）分文件

### Security

**D-4.1 认证: MVP 无认证**

- Docker network 隔离足够内测使用
- `/metrics` 通过 Prometheus 服务发现限制访问
- Phase 2 引入 API Key（简单 Header 校验）

**D-4.2 加密策略**

| 数据状态 | 方案 | 说明 |
|---------|------|------|
| 传输中 | TLS | Docker Compose 自签名 / K8s cert-manager |
| 存储中 | EBS 加密 | AWS GP3 默认 block-level encryption |
| 内存中 | 不加密 | Arrow 零拷贝要求裸 buffer 访问 |

**D-4.3 网络策略**

- **本地 Docker Compose：** 默认 bridge network，暴露 `8000`（metrics）+ `8265`（Ray Dashboard）
- **K8s 生产：** NetworkPolicy 预定义在 Helm Chart 中，values.yaml 默认关闭，生产部署时启用

### Deferred Decisions

- **前端 UI：** MVP 无前端。Grafana Dashboard（Prometheus）满足监控需求。数据浏览器推迟到 Phase 2
- **认证升级：** API Key → OAuth2/JWT 推迟到外部用户引入时
- **Actor 解耦：** Ray Queue 推迟到多租户场景（Phase 2）
- **Secrets 升级：** Vault 推迟到生产环境
- **NetworkPolicy：** K8s 策略预定义但默认关闭

### Decision Impact Analysis

**Implementation Sequence:**

1. **Day 1-2：** D-1.1（Schema）+ D-2.3（错误处理）+ D-3.4（配置）— 基础设施层
2. **Day 2-3：** D-1.5/D-1.6（缓存）+ D-2.2（Actor 通信）— 运行时层
3. **Day 3-4：** D-2.1（SDK）+ D-1.2/D-1.3/D-1.4（索引）— 功能层
4. **Day 4-5：** D-3.1（部署）+ D-3.2/D-3.3（CI/CD）— 发布层
5. **Day 5：** D-4.1/D-4.2/D-4.3（安全）— 加固层

**Cross-Component Dependencies:**

```
D-1.1 (Schema) ──→ D-2.1 (SDK) ──→ D-1.2/D-1.3 (索引)
                     │
D-3.4 (Config) ──→ D-2.3 (Error) ──→ D-2.2 (Actor)
                     │
D-4.2 (加密) ────→ D-3.1 (部署) ──→ D-3.2 (CI/CD)
```

Schema 定义是 SDK 和索引的前提；配置管理是错误处理和 Actor 通信的前提；加密策略约束部署拓扑。

## Implementation Patterns & Consistency Rules

### Pattern Categories Defined

**Critical Conflict Points Identified:** 18 个 AI Agent 可能产生分歧的冲突点，分布在 5 个类别。

### Naming Patterns

**Lance 表命名：** snake_case 复数

```
user_documents       ✅ 正确
UserDocuments        ❌ 错误
user_document        ❌ 单数
raw.user_documents   ❌ 不需要前缀
```

**Python 代码命名：**

| 元素 | 规则 | 正确示例 | 错误示例 |
|------|------|---------|---------|
| Ray Actor 类 | PascalCase + `Actor` 后缀 | `CatalogActor` | `Catalog`, `catalog_actor` |
| Metaflow Flow 类 | PascalCase + `Flow` 后缀 | `IngestFlow` | `Ingest`, `ingest_flow` |
| Pydantic Model | PascalCase + 语义后缀 | `TableSchema`, `IngestConfig` | `tableSchema`, `table_schema` |
| SDK 公开方法 | snake_case | `create_table()` | `createTable()` |
| Lance Schema 列名 | snake_case | `text_content`, `embedding_vector` | `textContent`, `TextContent` |
| 常量 | UPPER_SNAKE_CASE | `DEFAULT_CACHE_TTL` | `defaultCacheTTL`, `Default_Cache_Ttl` |
| 私有方法 | 单下划线前缀 | `_validate_schema()` | `validate_schema_` |

**Prometheus 指标命名：** `arrow_lake_{domain}_{metric}_{unit}`

```
arrow_lake_ingestion_rows_total         ✅
arrow_lake_embedding_duration_seconds   ✅
arrow_lake_quality_rejected_rows_total  ✅
arrow_lake_query_duration_seconds       ✅
arw_lake_ingest_rows                    ❌ 前缀/命名不一致
ingestion_rows                          ❌ 缺少前缀
```

### Structure Patterns

**包组织（按功能域）：**

```
arrow_lake/                    # 主包
├── __init__.py               # SDK 入口（ArrowLakeClient）
├── catalog/                  # Catalog 模块
│   ├── actor.py              # CatalogActor
│   ├── schema.py             # Pydantic → Arrow 转换
│   └── connection_pool.py    # DuckDB WAL 连接池
├── ingest/                   # 摄入模块
│   ├── pipeline.py           # IngestPipeline（Declarative）
│   ├── sources/              # 数据源连接器
│   │   ├── base.py           # 抽象基类
│   │   ├── local.py          # 本地文件
│   │   └── s3.py             # S3
│   └── validators.py         # 摄入时校验
├── quality/                  # 质量过滤模块
│   ├── filters.py            # QualityFilter 抽象 + 内置实现
│   └── dead_letter.py        # Dead-letter 持久化
├── embedding/                # 嵌入模块
│   ├── encoder.py            # 嵌入编码器（可插拔）
│   └── manager.py            # 索引管理
├── query/                    # 查询模块
│   ├── engine.py             # 双模查询引擎
│   ├── vector.py             # 向量搜索
│   ├── fts.py                # 全文搜索
│   └── hybrid.py             # 混合查询结果合并
├── ray_runtime/              # Ray 运行时
│   ├── placement.py          # Placement Group 管理
│   └── cache.py              # Object Store 缓存封装
├── config.py                 # Pydantic Settings（四层叠加）
├── exceptions.py             # 异常层次
└── metrics.py                # Prometheus 指标定义

flows/                        # Metaflow Flow 定义（包外）
├── ingest_flow.py
├── embedding_flow.py
└── search_flow.py

tests/
├── unit/
├── integration/              # Arrow 零拷贝边界测试
├── e2e/
└── conftest.py

deploy/
├── docker/
│   └── Dockerfile
├── compose/
│   └── docker-compose.yml
└── helm/
    └── arrow-lake/

configs/                      # YAML 配置（按环境）
├── dev.yaml
├── staging.yaml
└── prod.yaml
```

**测试命名与位置：**

```
tests/unit/test_catalog_actor.py          ✅ 与模块同名
tests/unit/test_connection_pool.py        ✅
tests/integration/test_arrow_boundary.py  ✅ 零拷贝边界测试
tests/e2e/test_full_pipeline.py           ✅ 端到端管线

tests/test_stuff.py          ❌ 不按层级分类
catalog_test.py              ❌ 测试不在 tests/ 下
```

### Format Patterns

**Arrow Schema 约定：**

```python
# ✅ 正确：snake_case 列名，新增列 nullable，向量列固定维度
pa.schema([
    pa.field("text_content", pa.string()),
    pa.field("image_bytes", pa.binary()),
    pa.field("embedding_vector", pa.list_(pa.float32(), 768)),
    pa.field("_source_url", pa.string()),        # 元数据列 _ 前缀
    pa.field("_ingested_at", pa.timestamp("us")),
    pa.field("_quality_score", pa.float32()),    # 新增列 nullable
])

# ❌ 错误
pa.field("textContent", pa.string())             # camelCase
pa.field("embedding", pa.list_(pa.float32()))     # 缺少维度
pa.field("quality_score", pa.float32(), nullable=False)  # 新增列强制非空
```

**日志格式（JSON + structlog）：**

```json
{
  "timestamp": "2026-04-11T10:30:00.000Z",
  "level": "INFO",
  "logger": "arrow_lake.ingest.pipeline",
  "message": "Ingestion completed",
  "correlation_id": "mf-run-abc123",
  "table": "user_documents",
  "rows": 1500,
  "duration_ms": 2340
}
```

- `correlation_id` = Metaflow `run_id`
- 额外字段按上下文附加（table, rows, duration_ms）
- 禁止 `print()` 和裸 `logging.info()`

**配置文件格式（YAML）：**

```yaml
# configs/dev.yaml
arrow_lake:
  storage:
    base_path: ./data/lance
    max_fragment_size_mb: 256
  cache:
    ttl_seconds: 1800
    blob_threshold_mb: 1
  ray:
    num_workers: 2
    gpu_per_worker: 0
  catalog:
    read_connections: 4
    write_connections: 1
```

- snake_case 键名，数值带单位后缀（`_mb`, `_seconds`）
- 禁止 `.json` 配置文件

### Communication Patterns

**Ray Actor 方法约定：**

| 规则 | 说明 | 正确 | 错误 |
|------|------|------|------|
| 方法名 | snake_case，动词开头 | `get_table()` | `getTable()`, `table_get` |
| 返回值 | Arrow Table 或 Pydantic model | `return pa.Table` | `return {"data": [...]}` |
| 对外方法 | `.remote()` 调用 | `actor.ingest.remote(data)` | `actor.ingest(data)` |
| 内部方法 | `_` 前缀 + 普通调用 | `self._validate(data)` | 公开内部方法 |
| 超时 | 配置化默认 30s | `ray.wait(ref, timeout=30)` | 无超时 |

**Metaflow Flow 约定：**

| 规则 | 说明 |
|------|------|
| Flow 命名 | PascalCase + `Flow` 后缀 |
| Step 命名 | snake_case：`start`, `transform`, `end` |
| 参数注入 | `@project` + Config YAML |
| 自包含 | `python flows/ingest_flow.py run` 独立可运行 |
| 日志 | 使用 Metaflow logger，自动关联 `run_id` |

### Process Patterns

**错误处理模式：**

```python
# ✅ 正确：自定义异常 + tenacity 重试
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type

class IngestionError(ArrowLakeError): ...

@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, max=30),
    retry=retry_if_exception_type(RayRuntimeError),
    reraise=True,
)
def _write_to_lance(self, table: pa.Table, table_name: str) -> None:
    ...
```

```python
# ❌ 错误：裸 except 吞掉异常
def write_to_lance(self, table, table_name):
    try:
        lance.write_dataset(table, table_name)
    except:  # 禁止
        pass
```

**管线执行模式：**

```python
# ✅ 正确：Declarative Config + 显式步骤 + 返回 Pydantic model
pipeline = IngestPipeline(
    source=S3Source(bucket="data", prefix="docs/"),
    filters=[TextLengthFilter(min_chars=10)],
    embed=True,
)
result = pipeline.run()  # IngestResult

# ❌ 错误：隐式步骤、无返回值
ingest_data("data/docs/", filters=True, embed=True)
```

**Arrow 零拷贝验证模式：**

```python
def assert_zero_copy(source_buf: pa.Buffer, target_buf: pa.Buffer) -> None:
    """验证组件边界间的 Arrow 共享内存。

    零拷贝定义：数据在组件间传递时，通过共享内存 buffer 引用
    而非拷贝。验证方式因边界而异：
    - Lance→DuckDB：DuckDB Arrow 扫描器使用共享内存
    - Lance→Daft：Daft 从 Arrow IPC 创建引用，非拷贝
    - Lance→PyTorch：pin_memory + CUDA async DMA
    """
    if source_buf is None or target_buf is None:
        return
    src_addr = source_buf.address
    tgt_addr = target_buf.address
    assert src_addr == tgt_addr, (
        f"Zero-copy violation: source=0x{src_addr:x}, "
        f"target=0x{tgt_addr:x}"
    )
```

### Enforcement Guidelines

**所有 AI Agent 必须：**

1. Ray Actor / Flow / Pydantic 类遵循后缀约定（`Actor`, `Flow`）
2. Arrow Schema 列名 snake_case，新增列 nullable
3. 异常使用自定义层次（`ArrowLakeError` 子类），禁止裸 `Exception`
4. 日志使用 JSON + `structlog` + `correlation_id`，禁止 `print()`
5. 测试按 `tests/unit/`, `tests/integration/`, `tests/e2e/` 三级组织
6. 配置使用 YAML + Pydantic Settings，禁止 `.json` 配置文件
7. Prometheus 指标遵循 `arrow_lake_{domain}_{metric}_{unit}` 格式
8. Actor 返回 Arrow Table 或 Pydantic model，不返回裸 dict
9. 对外方法用 `.remote()`，内部方法用 `_` 前缀
10. Metaflow Flow 自包含，`python flows/{name}_flow.py run` 可独立运行

**执行方式：**
- CI 门控：Ruff（lint）+ MyPy（type check）+ pytest（三级测试）
- PR review checklist 包含命名/结构/格式检查
- `conftest.py` 共享 fixture 确保测试一致性

## Project Structure & Boundaries

### Complete Project Directory Structure

```
arrow-lake/                           # 项目根目录
├── pyproject.toml                    # uv 项目配置 + 依赖声明
├── uv.lock                           # 锁文件（自动生成）
├── .python-version                   # Python 版本固定
├── ruff.toml                         # Ruff lint + format 配置
├── mypy.ini                          # MyPy 类型检查配置
├── .pre-commit-config.yaml           # pre-commit hooks
├── .env.example                      # 环境变量模板
├── .gitignore
├── CLAUDE.md                         # AI Agent 指令
│
├── arrow_lake/                       # ====== 主包 ======
│   ├── __init__.py                   # 公开 API：ArrowLakeClient
│   ├── _version.py                   # 版本号（单一来源）
│   ├── config.py                     # Pydantic Settings（四层叠加）
│   ├── exceptions.py                 # 异常层次定义
│   ├── metrics.py                    # Prometheus 指标注册 + 定义
│   │
│   ├── catalog/                      # --- Catalog 模块 ---
│   │   ├── __init__.py
│   │   ├── actor.py                  # CatalogActor（Ray Actor）
│   │   ├── schema.py                 # Pydantic → Arrow Schema 转换
│   │   ├── connection_pool.py        # DuckDB WAL 连接池
│   │   └── models.py                 # Table metadata Pydantic models
│   │
│   ├── ingest/                       # --- 摄入模块 ---
│   │   ├── __init__.py
│   │   ├── pipeline.py               # IngestPipeline（Declarative）
│   │   ├── models.py                 # IngestConfig, IngestResult
│   │   ├── sources/                  # 数据源连接器
│   │   │   ├── __init__.py
│   │   │   ├── base.py               # DataSource 抽象基类
│   │   │   ├── local.py              # 本地文件系统
│   │   │   └── s3.py                 # S3 / MinIO
│   │   └── validators.py             # 摄入时 Schema 校验
│   │
│   ├── quality/                      # --- 质量过滤模块 ---
│   │   ├── __init__.py
│   │   ├── base.py                   # QualityFilter 抽象接口
│   │   ├── builtin.py                # TextLengthFilter + ImageResolutionFilter
│   │   ├── dead_letter.py            # Dead-letter Lance 表写入
│   │   └── models.py                 # QualityReport Pydantic model
│   │
│   ├── embedding/                    # --- 嵌入模块 ---
│   │   ├── __init__.py
│   │   ├── encoder.py                # EmbeddingEncoder（可插拔）
│   │   ├── manager.py                # 索引构建 + 增量更新
│   │   └── models.py                 # EmbeddingConfig, IndexSpec
│   │
│   ├── query/                        # --- 查询模块 ---
│   │   ├── __init__.py
│   │   ├── engine.py                 # QueryEngine（5 种 SQL 模式路由）
│   │   ├── vector.py                 # 向量搜索（IVF_PQ）
│   │   ├── fts.py                    # 全文搜索（Tantivy）
│   │   ├── hybrid.py                 # 混合查询 + RRF 融合
│   │   └── models.py                 # SearchResult, QueryConfig
│   │
│   ├── ray_runtime/                  # --- Ray 运行时 ---
│   │   ├── __init__.py
│   │   ├── placement.py              # Placement Group 创建 + 管理
│   │   ├── cache.py                  # Object Store 缓存封装（LRU + TTL）
│   │   └── health.py                 # Actor 健康检查
│   │
│   └── sdk/                          # --- SDK 公开 API ---
│       ├── __init__.py
│       ├── client.py                 # ArrowLakeClient 主入口
│       ├── table.py                  # TableHandle（Fluent Builder）
│       └── search.py                 # SearchBuilder（Fluent 链式查询）
│
├── flows/                            # ====== Metaflow Flows ======
│   ├── __init__.py
│   ├── ingest_flow.py                # 摄入管线 Flow
│   ├── embedding_flow.py             # 嵌入管线 Flow
│   └── search_flow.py                # 搜索管线 Flow
│
├── tests/                            # ====== 测试 ======
│   ├── conftest.py                   # 共享 fixtures
│   ├── unit/
│   │   ├── test_config.py
│   │   ├── test_exceptions.py
│   │   ├── test_connection_pool.py
│   │   ├── test_schema_conversion.py
│   │   ├── test_quality_filters.py
│   │   ├── test_pipeline.py
│   │   ├── test_encoder.py
│   │   ├── test_query_engine.py
│   │   ├── test_cache.py
│   │   └── test_sdk_client.py
│   ├── integration/
│   │   ├── test_boundary_lance_daft.py
│   │   ├── test_boundary_daft_duckdb.py
│   │   ├── test_boundary_duckdb_pytorch.py
│   │   ├── test_boundary_cpu_gpu.py
│   │   ├── test_boundary_ray_object_store.py
│   │   └── test_boundary_cudf_arrow.py
│   ├── e2e/
│   │   ├── test_full_pipeline.py     # 4 步管线
│   │   └── test_ttv.py              # TTV 自动化验证
│   └── fixtures/
│       ├── sample_arrow_data.py
│       └── sample_lance_dataset.py
│
├── configs/                          # ====== 配置文件 ======
│   ├── dev.yaml
│   ├── staging.yaml
│   └── prod.yaml
│
├── deploy/                           # ====== 部署 ======
│   ├── docker/
│   │   └── Dockerfile
│   ├── compose/
│   │   ├── docker-compose.yml
│   │   ├── docker-compose.gpu.yml    # GPU overlay
│   │   └── prometheus.yml
│   └── helm/
│       └── arrow-lake/
│           ├── Chart.yaml
│           ├── values.yaml
│           ├── values-dev.yaml
│           └── templates/
│               ├── deployment.yaml
│               ├── service.yaml
│               ├── networkpolicy.yaml
│               └── prometheusrule.yaml
│
├── .github/                          # ====== CI/CD ======
│   └── workflows/
│       ├── ci.yml                    # PR 门控
│       ├── gpu-tests.yml             # Nightly + 手动 GPU 测试
│       └── release.yml               # Tag 触发发布
│
└── docs/                             # ====== 文档 ======
    ├── architecture.md
    └── examples/
        ├── quickstart.ipynb
        └── hybrid_search.ipynb
```

### Architectural Boundaries

**组件边界层次：**

```
┌─────────────────────────────────────────────────┐
│                  SDK Layer                       │
│  ArrowLakeClient → TableHandle → SearchBuilder   │
├──────────────────┬──────────────────────────────┤
│  CatalogActor    │  QueryEngine                  │
│  (Ray Actor)     │  (非 Actor，同步执行)          │
├──────────────────┼──────────────────────────────┤
│                  │  VectorSearch / FTSSearch      │
│  ConnectionPool  │  HybridFusion                  │
│  (DuckDB WAL)    │                                │
├──────────────────┴──────────────────────────────┤
│            Ray Runtime Layer                     │
│  PlacementGroup / ObjectStore Cache / Health     │
├─────────────────────────────────────────────────┤
│            Storage Layer (Lance)                 │
│  Tables / Indexes / Versions / Dead-letter       │
└─────────────────────────────────────────────────┘
```

**边界规则：**
- SDK 层不直接操作 Lance API — 通过 CatalogActor 或 QueryEngine
- QueryEngine 不依赖 Ray — 同步执行，OLAP 查询通过 Daft SQL（主路径）或 DuckDB（Catalog 查询）+ Lance 调用
- CatalogActor 是唯一写入 Catalog 的入口
- Ray Runtime 层被上层透明使用，不暴露 Ray API 给 SDK 用户

**数据边界：**

| 边界 | 数据格式 | 验证手段 |
|------|---------|---------|
| SDK → CatalogActor | Pydantic model | `model_validate()` |
| CatalogActor → Lance | `pa.Table` | `assert_zero_copy()` |
| Lance → Daft | Arrow IPC | Daft array 引用验证 |
| Daft → DuckDB | Arrow RecordBatch | 共享内存 buffer 验证（二级路径：catalog-only，非主分析链） |
| DuckDB → PyTorch | Arrow → Tensor | `pin_memory` + CUDA async DMA 验证（二级路径：catalog-only） |
| Metaflow → Ray Actor | Ray serialized | 自定义异常传播 |

**外部集成边界：**

| 集成 | 入口文件 | 通信方式 |
|------|---------|---------|
| Prometheus | `arrow_lake/metrics.py` | HTTP `/metrics` |
| S3 / MinIO | `arrow_lake/ingest/sources/s3.py` | boto3 / S3 API |
| Metaflow | `flows/*.py` | Python import + `@project` |
| Ray Dashboard | 内置 | HTTP `:8265` |

### Requirements to Structure Mapping

| FR 类别 | 需求 | 实现位置 |
|---------|------|---------|
| **摄入** | F-ING-01~09 | `arrow_lake/ingest/` + `flows/ingest_flow.py` |
| | 数据源连接 | `ingest/sources/{local,s3}.py` |
| | Schema 校验 | `ingest/validators.py` |
| | 去重 | `ingest/pipeline.py` |
| **处理** | F-PROC-01~09 | `arrow_lake/embedding/` + `arrow_lake/quality/` |
| | 质量过滤 | `quality/base.py`, `quality/builtin.py` |
| | Dead-letter | `quality/dead_letter.py` |
| | 嵌入计算 | `embedding/encoder.py` |
| | Ray 分布式 | `ray_runtime/placement.py` |
| **存储** | F-STOR-01~08 | `arrow_lake/catalog/` + Lance API |
| | 表管理 | `catalog/actor.py` |
| | 版本管理 | `catalog/actor.py` |
| | Compact | `catalog/actor.py` |
| **查询** | F-QRY-01~08 | `arrow_lake/query/` |
| | 向量搜索 | `query/vector.py` |
| | 全文搜索 | `query/fts.py` |
| | 混合查询 | `query/hybrid.py` |
| | 5 种 SQL 模式 | `query/engine.py` |
| **目录** | F-CAT-01~05 | `arrow_lake/catalog/` |
| | 连接池 | `catalog/connection_pool.py` |
| | Schema 转换 | `catalog/schema.py` |
| **编排** | F-ORCH-01~09 | `flows/` + `arrow_lake/config.py` |
| | Metaflow Flows | `flows/{ingest,embedding,search}_flow.py` |
| | @schedule | Metaflow `@schedule` 装饰器 |
| **DevEx** | F-DEV-01~07 | `arrow_lake/sdk/` + `configs/` |
| | SDK 入口 | `sdk/client.py` |
| | Fluent 查询 | `sdk/table.py`, `sdk/search.py` |
| **质量** | F-QUA-01~05 | `arrow_lake/quality/` |
| **可观测** | F-OBS-01~06 | `arrow_lake/metrics.py` |

### Cross-Cutting Concerns Locations

| 关注点 | 实现位置 | 跨模块影响 |
|--------|---------|-----------|
| Arrow 零拷贝纪律 | `tests/integration/test_boundary_*.py` | 全部 6 个 Arrow 边界 |
| 配置管理（四层叠加） | `arrow_lake/config.py` | 所有模块通过 `Settings` 注入 |
| 结构化日志（JSON + correlation_id） | `arrow_lake/config.py`（structlog 配置） | 所有模块统一 logger |
| 成本追踪 | `arrow_lake/metrics.py` + `ray_runtime/` | Ray 注解 + Prometheus 指标 |
| Schema 演进 | `arrow_lake/catalog/schema.py` | Catalog + Ingest + Query |
| 异常层次 | `arrow_lake/exceptions.py` | 所有模块 |
| Prometheus 指标 | `arrow_lake/metrics.py` | Ingest + Embedding + Query + Catalog |

### Integration Points

**内部通信流：**

```
用户 Python 代码
    │
    ▼
ArrowLakeClient (sdk/client.py)
    │
    ├─→ TableHandle.create() ──→ CatalogActor.create_table.remote()
    │                              └─→ ConnectionPool (write) → Lance
    │
    ├─→ TableHandle.ingest() ──→ IngestPipeline.run()
    │                              ├─→ DataSource.read() → pa.Table
    │                              ├─→ QualityFilter.filter() → pa.Table
    │                              ├─→ CatalogActor.append.remote() → Lance
    │                              └─→ IngestResult (Pydantic)
    │
    ├─→ TableHandle.search() ──→ SearchBuilder.vector().to_arrow()
    │                              └─→ QueryEngine.execute()
    │                                  ├─→ VectorSearch (IVF_PQ)
    │                                  ├─→ FTSSearch (Tantivy)
    │                                  └─→ HybridFusion (RRF) → pa.Table
    │
    └─→ EmbeddingFlow.run() ──→ Metaflow orchestrates
                                  ├─→ Ray Actor (encoder) on Placement Group
                                  └─→ CatalogActor.create_index.remote()
```

**外部集成流：**

```
Prometheus ←── HTTP /metrics ─── metrics.py (prometheus_client)

S3/MinIO  ←── boto3 ─── ingest/sources/s3.py

Metaflow CLI ──→ python flows/ingest_flow.py run
                    └─→ @project → configs/{env}.yaml → Settings
```

## Architecture Validation Results

### Coherence Validation ✅

**Decision Compatibility:**
- DARMU Stack（Daft + Argo + Ray + Metaflow + uv）内部全链路 Arrow 原生兼容
- Ray Placement Group + Object Store 满足零拷贝前提
- DuckDB WAL 连接池（Catalog-only）+ Daft SQL（OLAP 主路径）+ Lance Extension 读写分离无冲突
- Pydantic-first Schema → Arrow 类型映射与 Lance 列式存储兼容
- Hybrid SDK（Fluent 同步 + Declarative 编排）与 Ray Actor + Metaflow 协调一致
- Docker Compose → Mini Cluster → Helm Chart 演进路径清晰
- 已解决张力：cuDF→Arrow 受控拷贝点（FR 冲突表记录）、QueryEngine 同步 vs CatalogActor 异步（SDK 层协调）

**Pattern Consistency:**
- 命名约定（Actor/Flow 后缀、snake_case 列名）与 Ray、Metaflow 惯例一致
- JSON + correlation_id 日志与 Metaflow run_id 对齐
- Prometheus `arrow_lake_{domain}_{metric}_{unit}` 覆盖全部 15 指标
- 10 条强制规则贯穿所有模块

**Structure Alignment:**
- 包组织按功能域划分，与 FR 类别映射完全对应
- 测试三级目录与测试策略分层一致
- `flows/` 包外独立，符合 Metaflow Flow 自包含最佳实践

### Requirements Coverage Validation ✅

**Functional Requirements Coverage: 68/68 (100%)**

> **FR 来源说明：** 原始 PRD 定义 57 条 FR（F-ING-01~09, F-PROC-01~09, F-STOR-01~08, F-QRY-01~08, F-CAT-01~05, F-ORCH-01~04/05a/05b/05c/06~09, F-DEV-01~07）。本架构通过 ADR-02 新增 11 条派生 FR（F-QUA-01~05 质量管控 + F-OBS-01~06 可观测性）。合计 68 条。

| FR 类别 | 数量 | 覆盖 | 备注 |
|---------|------|------|------|
| 摄入 (F-ING-01~09) | 9 | ✅ | |
| 处理 (F-PROC-01~09) | 9 | ✅ | |
| 存储 (F-STOR-01~08) | 8 | ✅ | |
| 查询 (F-QRY-01~08) | 8 | ⚠️ | F-QRY-01 HNSW 策略降级为 IVF_PQ（见 H3）；F-QRY-05 流式结果推迟到 Phase 2（见 H4） |
| 目录 (F-CAT-01~05) | 5 | ✅ | |
| 编排 (F-ORCH-01~09) | 9 | ⚠️ | F-ORCH-09 事件溯源推迟到 Phase 2（见 H5） |
| DevEx (F-DEV-01~07) | 7 | ✅ | |
| 质量 (F-QUA-01~05) | 5 | ✅ | ADR-02 新增 |
| 可观测 (F-OBS-01~06) | 6 | ✅ | ADR-02 新增 |

**已知覆盖缺口（Phase 2 补充）：**
- **H3 (F-QRY-01):** PRD 定义 HNSW（<1M 行）+ IVF_PQ（1M+ 行）自适应策略。MVP 统一使用 IVF_PQ（Lance 内置），<1M 行延迟可能略逊于 HNSW 但可接受。Phase 2 考虑引入 Lance HNSW 支持。
- **H4 (F-QRY-05):** 流式结果（`fetch_record_batch_reader`）要求常量内存。MVP 所有查询返回完整 `pa.Table`。**输入侧已优化（2026-04-15）：** `LanceStorageManager.scan_dataset()` 返回 `RecordBatchReader` 流式读取，避免全量物化。OlapSearchBridge 自动检测 JOIN/子查询场景降级。输出侧流式仍推迟 Phase 2。
- **H5 (F-ORCH-09):** 事件溯源/审计日志。MVP 通过 structlog + correlation_id 记录操作日志。Phase 2 引入不可变事件存储。

**Non-Functional Requirements Coverage: 7/7 域全部覆盖**

| NFR | 核心约束 | 架构支撑 |
|-----|---------|---------|
| 性能 | 向量 <10ms, 零拷贝 >90% | IVF_PQ + 5 级 Lazy Eval + Arrow Buffer 验证 |
| 可靠性 | 自动恢复 >95% | tenacity 重试 + Lance version rollback |
| 可扩展性 | 扩容 <5min | Ray AutoScale v2 + Spot GPU |
| 成本 | <$500/月 | Elastic Burst $440/mo + ResourceQuota |
| 易用性 | 上手 <30min | Docker Compose TTV <45min + Hybrid SDK |
| 安全 | 数据加密 | TLS + EBS + Docker 网络隔离 |
| 可观测性 | 17 指标 | Prometheus + structlog |

### Implementation Readiness Validation ✅

**Decision Completeness:**
- 20 项架构决策全部有结论、版本约束、理由和影响分析
- 6 项推迟决策有 Earliest Phase 标记
- 2 项 ADR（Catalog 架构 + MVP P0 补充）有完整辩论记录

**Structure Completeness:**
- ~60 个文件/目录明确定义，含模块职责说明
- 6 个 Arrow 边界测试文件对应 6 个数据边界
- FR → 文件映射表覆盖全部 68 条需求

**Pattern Completeness:**
- 18 个 AI Agent 冲突点覆盖 5 类（命名/结构/格式/通信/流程）
- 10 条强制执行规则 + 正确/错误示例对照
- 零拷贝验证模式（`assert_zero_copy`）+ 管线执行模式 + 错误处理模式

### Gap Analysis Results

**Critical Gaps: 无**

**Important Gaps（2 项，不阻塞实现）：**

| # | 差距 | 解决方案 |
|---|------|---------|
| G1 | 辅助库版本未在依赖矩阵列出 | 实现时 `pyproject.toml` 定义（structlog, tenacity, pydantic, boto3） |
| G2 | Dead-letter 表命名 | 确认：`{table_name}_dead_letter`（每表独立目录） |

**Nice-to-Have Gaps（3 项，Phase 2 补充）：**

| # | 差距 | 说明 |
|---|------|------|
| G3 | Arrow Schema 版本迁移策略 | Lance schema evolution 迁移脚本模板 |
| G4 | Grafana Dashboard 模板 | 监控面板预配置 JSON |
| G5 | `@schedule` cleanup Cron 表达式 | 版本清理调度策略 |

### Architecture Completeness Checklist

**✅ Requirements Analysis**
- [x] 项目上下文全面分析（57 FR from PRD + 27 NFR + 11 ADR-02 derived FR）
- [x] 规模与复杂度评估（Medium, ~15 组件）
- [x] 技术约束识别（6 铁律）
- [x] 跨组件关注点映射（5 项）
- [x] 风险识别与评估（R1-R9）

**✅ Architectural Decisions**
- [x] 2 项 ADR 完成（Catalog 架构 + MVP P0 补充）
- [x] 20 项决策记录（数据/API/基础设施/安全）
- [x] 技术栈版本验证（Daft >= 0.7.8 等，Step 3 部分验证推迟到实现前）
- [x] FR 冲突识别与解决（4 项）
- [x] 优先级调整（5 项提升 P0, 4 项推迟 P1）

**✅ Implementation Patterns**
- [x] 命名约定建立（7 类命名规则）
- [x] 结构模式定义（包组织 + 测试组织）
- [x] 格式模式指定（Arrow Schema + 日志 + 配置）
- [x] 通信模式规定（Actor + Metaflow）
- [x] 流程模式文档化（错误 + 管线 + 零拷贝验证）
- [x] 10 条强制规则 + 执行方式

**✅ Project Structure**
- [x] 完整目录结构定义（~60 文件）
- [x] 组件边界建立（5 层架构）
- [x] 集成点映射（6 个数据边界 + 4 个外部集成）
- [x] FR → 文件映射完成（68/68，含 11 条架构派生 FR）
- [x] 跨组件关注点定位

**✅ Validation**
- [x] 一致性验证通过
- [x] 需求覆盖 100%
- [x] 实现就绪性确认
- [x] 差距分析完成（0 Critical, 2 Important）

### Architecture Readiness Assessment

**Overall Status: READY FOR IMPLEMENTATION**

**Confidence Level: HIGH** — 基于：
- 50 次深度头脑风暴覆盖 10 个维度
- 2 项完整 ADR 辩论
- 68 条 FR 全映射
- 20 项架构决策 + 18 个冲突点解决
- 6 个 Arrow 零拷贝边界测试策略

**Key Strengths:**
1. Arrow 零拷贝全链路设计有量化验证手段（Buffer 地址比对）
2. 5 级 Lazy Evaluation 涵盖从存储到查询全部优化点
3. Catalog 连接池方案（ADR-01）解决了单例 vs 高 QPS 的核心矛盾；Daft SQL 作为主 OLAP 引擎解耦了分析负载与 Catalog 元数据
4. MVP P0 质量管控 + 可观测性补充（ADR-02）确保端到端可验证
5. 渐进复杂度 5 级 API 保证零代码改动从本地到 K8s

**Areas for Future Enhancement:**
1. 多租户隔离（Phase 2，峡谷 2）
2. 前端数据浏览器（Phase 2）
3. Arrow Schema 迁移工具（Phase 2）
4. GPU 成本自动化管控闭环（ResourceQuota + 自动降级）

**Performance Baseline Note:**
NF-PERF-01 定义的"<10ms 向量搜索延迟"基线需重新校准。原 PRD 基于 HNSW 索引设计，MVP 统一采用 IVF_PQ 索引。IVF_PQ 在 <1M 行场景下延迟可能略高于 HNSW，但在 1M+ 行场景下 IVF_PQ 优势明显。建议实现后使用真实数据重新建立 P50/P99 基线。

**Cost Estimate Note:**
$440/月 Elastic Burst 是粗估，假设 AWS us-east-1、Spot GPU 实例。分解：2x T4 Spot ~$200 + 32 vCPU Spot ~$120 + 存储 ~$60 + Argo/Prometheus ~$60 = ~$440。实际成本取决于使用模式和区域。实现后应根据实际用量校准。

**Deferred Decisions Requiring Pre-Implementation Resolution:**
| 决策 | 影响 | 建议时机 |
|------|------|---------|
| HNSW vs IVF_PQ 策略 | F-QRY-01 P0 | 确认 MVP 仅用 IVF_PQ，接受小数据集延迟略增 |
| 流式结果接口 | F-QRY-05 P0 | 确认 MVP 返回 pa.Table，流式推迟 Phase 2 |
| @schedule cron 表达式 | F-ORCH-06 P0 | 接受 MVP 手动触发清理，cron Phase 2 |
| Arrow Schema 迁移策略 | F-PROC-07 P0 | 接受 MVP 仅 add_columns，alter 迁移 Phase 2 |
| 辅助库精确版本 | 所有模块 | 推迟到 pyproject.toml 定义时统一验证 |

### Implementation Handoff

**AI Agent Guidelines:**
1. 遵循本文档所有架构决策，不自行发挥
2. 严格遵循 10 条强制规则（命名/格式/日志/测试等）
3. 尊重组件边界 — SDK 层不直接操作 Lance API
4. 所有 Arrow 边界必须通过 `assert_zero_copy()` 验证
5. PR review checklist 参照本文档 Enforcement Guidelines

**First Implementation Priority:**
1. `arrow_lake/config.py` + `arrow_lake/exceptions.py` — 基础设施
2. `arrow_lake/catalog/connection_pool.py` — DuckDB WAL 连接池（Catalog-only，Story 1.2 Spike 验证）
3. `arrow_lake/catalog/actor.py` — CatalogActor（Ray Actor，支持 namespace 参数用于未来多租户隔离）
4. `arrow_lake/sdk/client.py` — ArrowLakeClient 入口
5. 零拷贝边界验证（6 个 integration test，使用组件边界验证而非简单地址比对）

> **架构决策更新（2026-04-12）：** DuckDB 角色从 OLAP + Catalog 重新定义为 Catalog-only 存储。Daft SQL 晋升为主 OLAP 引擎（参考 CloudKitchens DREAM stack 和字节跳动火山引擎生产实践）。此变更反映在 80 stories 的 epics.md 中，关键影响 Story 1.2（Spike 验证）、Story 1.6（连接池简化）、Story 7.6（双 SQL 接口）。
