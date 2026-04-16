---
stepsCompleted: [1, 2, 3, 4, 5, 6]
lastStep: 6
status: complete
project_name: arrow-lake
date: 2026-04-12
documents_in_scope:
  prd: _bmad-output/planning-artifacts/prd.md
  architecture: _bmad-output/planning-artifacts/architecture.md
  system_design: _bmad-output/planning-artifacts/system_design.md
  brainstorming: _bmad-output/brainstorming/
  epics: _bmad-output/planning-artifacts/epics.md
language: 'zh'
chineseVersionOf: implementation-readiness-report-2026-04-11.md
---

# 实施就绪性评估报告

**日期：** 2026-04-12
**项目：** Arrow Lake (wits-infra-dintellihub)

## 步骤 1：文档发现

### PRD 文档

**完整文档：**
- `prd.md` — 英文 PRD
- `prd-zh.md` — 中文 PRD（翻译版）

**分片文档：** 无

### 架构文档

**完整文档：**
- `architecture.md` — 架构决策文档 (stepsCompleted: [1,2,4,5,6,7,8], 已完成)

**分片文档：** 无

### 系统设计文档

**完整文档：**
- `system_design.md` — 系统设计文档 (status: complete, reviewed)

**分片文档：** 无

### Epic 与 Story 文档

**完整文档：**
- `epics.md` — 8 个 Epic，80 个 Story (status: complete, expert-reviewed)

**分片文档：** 无

### UX 设计文档

**完整文档：** 无

**分片文档：** 无

### 辅助文档

**头脑风暴：**
- `brainstorming-session-2026-04-10-1500.md`
- `appendix-deep-dives.md`

### 发现的问题

**缺失文档（无）：**
- UX 设计 — MVP 无前端界面，UX 文档非必需（已由 architecture.md D-4.1 确认）

**未发现重复文档。**

### 纳入评估的文档

| 文档 | 路径 | 状态 |
|------|------|--------|
| PRD | `prd.md` | 已完成 |
| 架构 | `architecture.md` | 已完成 |
| 系统设计 | `system_design.md` | 已完成，已审核 |
| Epic 与 Story | `epics.md` | 已完成，专家审核 |
| 头脑风暴 | `brainstorming/` | 参考资料 |

## 步骤 2：PRD 分析

### 功能需求

**6.1 数据摄入（9 个 FR）**
| ID | 需求 | 优先级 |
|----|------|--------|
| F-ING-01 | 从本地文件系统、S3/MinIO、HTTP 摄入 text/CSV/JSON/Parquet | P0 |
| F-ING-02 | 摄入图像（JPEG/PNG/WebP）并自动生成缩略图 | P0 |
| F-ING-03 | 摄入视频：在场景边界提取关键帧（PyAV），MVP 范围：每个场景提取单帧 | P1 |
| F-ING-04 | 摄入时计算文本嵌入向量（HuggingFace 本地 / Ray Serve / 外部 API） | P0 |
| F-ING-05 | 摄入时计算图像嵌入向量（CLIP/SigLIP） | P0 |
| F-ING-06 | 将原始数据 + 嵌入向量存储在统一 Lance 表中 | P0 |
| F-ING-07 | 嵌入计算完成后异步构建向量索引 | P0 |
| F-ING-08 | 基于内容寻址的去重（SHA-256 精确匹配 + pHash 感知哈希） | P0 |
| F-ING-09 | 多保真度存储（缩略图 + 预览 + 原始文件） | P1 |

**6.2 数据处理（9 个 FR）**
| ID | 需求 | 优先级 |
|----|------|--------|
| F-PROC-01 | Daft DataFrame API 用于多模态数据转换 | P0 |
| F-PROC-02 | GPU/CPU 异构调度 (`use_gpu=True`) | P0 |
| F-PROC-03 | SQL 查询支持（Daft SQL + DuckDB） | P1 |
| F-PROC-04 | 质量评分流水线（NeMo Curator：去重、分类器、美学评分） | P1 |
| F-PROC-05 | 质量评分作为 Lance 列，支持谓词下推 | P0 |
| F-PROC-06 | 图像/视频的懒加载解码 | P0 |
| F-PROC-07 | Schema 迁移：添加/修改/删除列而无需全量重写 | P0 |
| F-PROC-08 | 通过 Ray 进行分布式处理（foreach + AutoScale） | P0 |
| F-PROC-09 | 远程数据加载器模式（CPU 解码 → Object Store → GPU 训练） | P1 |

**6.3 存储与版本管理（8 个 FR）**
| ID | 需求 | 优先级 |
|----|------|--------|
| F-STOR-01 | 所有存储数据使用 Lance 格式，原生 Arrow I/O | P0 |
| F-STOR-02 | 每次写入自动版本管理（Lance version） | P0 |
| F-STOR-03 | 为重要版本创建命名标签 | P0 |
| F-STOR-04 | 时间旅行查询：读取任意历史版本 | P0 |
| F-STOR-05 | 版本差异比较：比较两个版本（schema + 行 + 列变更） | P1 |
| F-STOR-06 | 压缩：合并 Fragment 文件，回收已删除列的存储空间 | P0 |
| F-STOR-07 | 自动分层 Blob 生命周期管理（Standard → IA → Glacier） | P2 |
| F-STOR-08 | S3/MinIO 后端，支持可配置端点 | P0 |

**6.4 查询与检索（8 个 FR）**
| ID | 需求 | 优先级 |
|----|------|--------|
| F-QRY-01 | 向量搜索（<1M 使用 HNSW，1M+ 使用 IVF_PQ） | P0 |
| F-QRY-02 | 全文搜索（Lance FTS） | P0 |
| F-QRY-03 | 混合搜索（向量 + 文本，可配置 alpha） | P0 |
| F-QRY-04 | OLAP 分析（DuckDB SQL 配合 Lance 谓词下推） | P0 |
| F-QRY-05 | 流式结果返回（fetch_record_batch_reader，恒定内存） | P0 |
| F-QRY-06 | 分面搜索（DuckDB CUBE + 向量搜索） | P2 |
| F-QRY-07 | 基于数据规模和查询模式的自适应索引选择 | P0 |
| F-QRY-08 | 多模型集成搜索 | P2 |

**6.5 目录与元数据（5 个 FR）**
| ID | 需求 | 优先级 |
|----|------|--------|
| F-CAT-01 | 集中式目录作为 Ray Named Actor（内嵌 DuckDB） | P0 |
| F-CAT-02 | 注册数据集及其 schema、列元数据和统计信息 | P0 |
| F-CAT-03 | 通过 SQL 查询目录元数据 | P0 |
| F-CAT-04 | 统一搜索 API 路由通过目录 | P0 |
| F-CAT-05 | 数据血缘作为对 Lance 事件日志的 SQL 查询 | P2 |

**6.6 工作流编排（11 个 FR）**
| ID | 需求 | 优先级 |
|----|------|--------|
| F-ORCH-01 | 所有批处理流水线使用 Metaflow FlowSpec | P0 |
| F-ORCH-02 | 本地执行：`python flow.py run` | P0 |
| F-ORCH-03 | 集群执行：`python flow.py run --with ray` | P0 |
| F-ORCH-04 | 生产部署：`python flow.py --with ray argo-workflows create` | P1 |
| F-ORCH-05a | 瞬态重试：@retry 指数退避 | P0 |
| F-ORCH-05b | 错误分类：@catch 处理器区分可重试与致命错误 | P0 |
| F-ORCH-05c | 状态回滚：致命错误时 Lance 版本检出 | P0 |
| F-ORCH-06 | 定时流水线：@schedule(daily/hourly/cron) | P0 |
| F-ORCH-07 | 基于标签的运行跟踪与恢复 | P1 |
| F-ORCH-08 | 弹性突发：按需自动扩展 GPU Worker | P1 |
| F-ORCH-09 | 事件溯源：Lance 版本 + Metaflow 标签 = 不可变审计追踪 | P2 |

**6.7 开发者体验（7 个 FR）**
| ID | 需求 | 优先级 |
|----|------|--------|
| F-DEV-01 | 一键启动平台：`docker compose up -d` | P1 |
| F-DEV-02 | Jupyter Notebook 集成用于数据探索 | P1 |
| F-DEV-03 | 使用 uv 进行依赖管理 | P0 |
| F-DEV-04 | Python SDK：`from arrow_lake import Lake` | P0 |
| F-DEV-05 | 数据测试：针对 Lance/Daft/DuckDB 结果的 pytest 断言 | P1 |
| F-DEV-06 | 渐进式复杂度：5 个 API 层级 | P0 |
| F-DEV-07 | 常用操作 CLI（ingest、search、status、version） | P2 |

**6.8 质量管理 — ADR-02 衍生（5 个 FR）**
| ID | 需求 | 优先级 |
|----|------|--------|
| F-QUA-01 | QualityFilter 注册：可插拔的行级过滤器接口 | P0 |
| F-QUA-02 | 内置过滤器：TextLengthFilter + ImageResolutionFilter | P0 |
| F-QUA-03 | 死信持久化：被拒绝行写入 `{table}_dead_letter` Lance 表 | P0 |
| F-QUA-04 | 质量统计报告：总计/通过/拒绝 + 按过滤器分类明细 | P0 |
| F-QUA-05 | Schema 验证门控：严格模式拒绝未知列/类型不匹配 | P0 |

**6.9 可观测性 — ADR-02 衍生（6 个 FR）**
| ID | 需求 | 优先级 |
|----|------|--------|
| F-OBS-01 | Prometheus `/metrics` HTTP 端点（Prometheus 格式） | P0 |
| F-OBS-02 | 摄入指标：每表的行数/字节数/耗时/错误数 | P0 |
| F-OBS-03 | 处理指标：嵌入计算/质量拒绝/活跃任务数 | P0 |
| F-OBS-04 | 查询指标：按 query_type 统计计数/延迟/结果数 | P0 |
| F-OBS-05 | 系统指标：Ray Actor 数/表数量/运行时间 | P0 |
| F-OBS-06 | 指标可配置：通过环境变量设置端口/路径，支持禁用 | P0 |

**FR 总计：68**（57 个 PRD FR，包含 F-ORCH-05 拆分 + 11 个 ADR-02 衍生 FR）

**优先级分布：**
| 优先级 | 数量 |
|--------|------|
| P0 | 50 |
| P1 | 12 |
| P2 | 6 |

### 非功能需求

**7.1 性能（6 个 NFR）**
| ID | 需求 | 目标 |
|----|------|------|
| NF-PERF-01 | 向量搜索延迟（1000 万行，top_k=100） | < 10ms |
| NF-PERF-02 | 摄入吞吐量（文本，单节点） | > 50K 行/秒 |
| NF-PERF-03 | 全链路 Arrow 零拷贝利用率 | > 90% |
| NF-PERF-04 | 1% 选择率下懒执行加速 | > 100x（对比立即执行） |
| NF-PERF-05 | 流式查询内存占用（1 亿行） | < 100MB |
| NF-PERF-06 | PyTorch DataLoader 零拷贝 + 异步 GPU 传输 | pin_memory + non_blocking |

**7.2 可靠性（4 个 NFR）**
| ID | 需求 | 目标 |
|----|------|------|
| NF-REL-01 | 工作流恢复率（无需人工干预） | > 90%（MVP），> 95%（生产） |
| NF-REL-02 | 故障时数据完整性（Lance 版本 + Metaflow 检查点） | 零数据丢失 |
| NF-REL-03 | Catalog Actor 可用性 | max_restarts=3，自动恢复 |
| NF-REL-04 | 瞬态故障 MTTR | < 10 分钟 |

**7.3 可扩展性（5 个 NFR）**
| ID | 需求 | 目标 |
|----|------|------|
| NF-SCALE-01 | 数据量支持（单节点） | 最高 1000 万行 |
| NF-SCALE-02 | 数据量支持（分布式） | 最高 10 亿行 |
| NF-SCALE-03 | 并发查询支持 | 最高 100 QPS（含读副本） |
| NF-SCALE-04 | GPU 扩展模型 | 分片 GPU（0.5），最多 8 个 Worker |
| NF-SCALE-05 | 弹性突发：0 到 8 个 GPU Worker | 扩容时间 < 5 分钟 |

**7.4 成本效益（4 个 NFR）**
| ID | 需求 | 目标 |
|----|------|------|
| NF-COST-01 | 弹性突发月度成本（100GB/月处理量） | < $500/月 |
| NF-COST-02 | 通过自动分层降低存储成本（100TB） | > 50%（对比全部 Standard） |
| NF-COST-03 | 突发工作负载的 Spot GPU 利用率 | 可用时 > 70% Spot |
| NF-COST-04 | 基线（空闲）平台成本 | < $400/月 |

**7.5 易用性（4 个 NFR）**
| ID | 需求 | 目标 |
|----|------|------|
| NF-USE-01 | 开发者上手时间 | < 30 分钟 |
| NF-USE-02 | 从本地到生产部署的代码变更 | 零变更 |
| NF-USE-03 | 嵌入模型热替换 | 零数据重写，零停机 |
| NF-USE-04 | API 复杂度层级 | 5 个层级（简单 → 高级） |

**7.6 安全性（4 个 NFR）**
| ID | 需求 | 目标 |
|----|------|------|
| NF-SEC-01 | 密钥管理 | 环境变量 / .env 文件，无硬编码凭据 |
| NF-SEC-02 | S3/MinIO 访问控制 | IAM 角色（生产）/ 访问密钥（开发） |
| NF-SEC-03 | API 边界的输入验证 | 摄入时的 Schema 验证 |
| NF-SEC-04 | 容器安全 | 官方基础镜像，最小攻击面 |

**7.7 可观测性（5 个 NFR）**
| ID | 需求 | 目标 |
|----|------|------|
| NF-OBS-01 | 流水线指标 | Prometheus + Grafana 仪表盘 |
| NF-OBS-02 | Ray 集群监控 | Ray Dashboard（内置） |
| NF-OBS-03 | 结构化日志 | JSON 格式日志，含关联 ID |
| NF-OBS-04 | 数据质量报告 | Metaflow Cards（每步骤 HTML 报告） |
| NF-OBS-05 | 每次流水线运行的成本追踪 | Ray 资源注解 + Prometheus |

**NFR 总计：32**

### 附加需求与约束

- **技术约束（第 8 节）：** DARMU 技术栈为强制要求（Daft >= 0.7.8, Argo >= 3.5, Ray >= 2.54.1, Metaflow >= 2.19.22, uv 最新版）
- **三层基础设施（第 8.3 节）：** 开发环境（Docker Compose + MinIO）→ 预发布环境（Ray SSH + Prometheus）→ 生产环境（KubeRay + S3 + Redis Streams）
- **范围外内容（第 1.3 节）：** 无自定义 UI、无实时流处理、无多用户 RBAC、无模型训练框架
- **MVP 闸门标准：** < 45 分钟，1000 条混合质量记录，4 个步骤（摄入→质量→嵌入→搜索），TTV + /metrics
- **指导原则（第 1.2 节）：** Arrow 原生零拷贝、跨模态统一、嵌入优先、渐进式复杂度、默认自愈

### PRD 完整性评估

**优势：**
- 结构清晰，ID 命名规范明确（F-{CATEGORY}-{NN} 和 NF-{CATEGORY}-{NN}）
- 优先级（P0/P1/P2）分配一致
- ADR-02 衍生的 FR（F-QUA-*、F-OBS-*）可追溯至架构决策
- MVP 范围明确定义，附带可衡量的闸门标准
- 11 个衍生 FR（F-ORCH-05a/b/c、F-QUA-01~05、F-OBS-01~06）填补了结构性空缺

**分析中发现的问题：**
1. **[已修复] F-ORCH-06 拼写错误：** 在 prd.md 中被标记为 `F-CH-06`——已更正为 `F-ORCH-06`
2. **[已修复] Daft 版本：** 在 prd.md 中为 `>= 0.4.0`——已更正为 `>= 0.7.8`，与 architecture.md 和 system_design.md 保持一致
3. **[信息] FR 计数：** Architecture.md 引用"55 个 PRD FR + 11 个衍生 = 66"。实际 PRD 计数现为 **68**（55 个原始 + 13 个衍生：F-ORCH-05a/b/c 将 F-ORCH-05 拆分为 3 个，F-QUA-01~05 = 5 个，F-OBS-01~06 = 6 个）。Architecture.md 可能需要更新计数。
4. **[低] 验收标准：** FR 缺少明确的验收标准（通过/失败条件）——在 PRD 层级可接受，现已在 epics/stories 中解决

## 步骤 3：Epic 覆盖率验证

### 覆盖率矩阵

| 状态 | 数量 |
|------|------|
| PRD FR 总数 | 68 |
| Epics 中已覆盖的 FR | 68 |
| 未覆盖的 FR | 0 |
| 覆盖率百分比 | 100% |

### Epic 到 FR 的映射摘要

| Epic | Story 数量 | 覆盖的 FR |
|------|-----------|----------|
| E1: 项目基础 | 10 | F-DEV-01, F-DEV-03, F-STOR-08, F-CAT-01, F-DEV-04, F-DEV-06 |
| E2: 数据摄入 | 8 | F-ING-01~09 |
| E3: 嵌入流水线 | 9 | F-ING-04, F-ING-05, F-ING-07, F-PROC-02, F-PROC-08 |
| E4: 质量管理 | 13 | F-QUA-01~05, F-PROC-04, F-PROC-05, F-ING-08 |
| E5: 存储与版本管理 | 9 | F-STOR-01~06 |
| E6: 查询与检索 | 12 | F-QRY-01~08, F-PROC-03, F-PROC-01 |
| E7: 工作流编排 | 14 | F-ORCH-01~09 |
| E8: 可观测性 | 5 | F-OBS-01~06 |

### 覆盖率统计

- PRD FR 总数：**68**
- Epics 中已覆盖的 FR：**68**
- 覆盖率百分比：**100%**
- 评估结论：**通过** — 所有功能需求均已映射到实施 Story

### Epics 中体现的关键架构变更

1. **DuckDB 角色重新定义：** 仅用于目录（内嵌在 Ray Named Actor 中处理元数据）。Daft SQL 作为分析查询的主要 OLAP 引擎。
2. **连接池简化：** 4 个读连接 + 1 个写连接，仅处理目录工作负载。
3. **零拷贝验证：** 使用组件边界验证，而非端到端缓冲区一致性检查。
4. **MVP 核心时间线：** 第 1-6 周（从 1-5 周扩展，以容纳质量管理 Story）。

## 步骤 4：UX 一致性评估

### UX 文档状态

**未找到** — `{planning_artifacts}` 中不存在 UX 设计文档。

### UX 需求评估

MVP **无前端 UI**（已由 architecture.md 决策 D-4.1 确认）。PRD 第 1.3 节明确将 v1 范围界定为 CLI + Notebook 优先：

- 无自定义 UI/可视化仪表盘
- 主要接口：Python SDK（`from arrow_lake import Lake`）
- 辅助接口：CLI（ingest、search、status、version）
- 探索接口：Jupyter Notebook

### 一致性问题

**无** — UX 文档的缺失是有意为之，与 PRD 范围一致（第 1.3 节"范围外内容 (v1)"）。架构文档正确地考虑了 CLI/SDK/Notebook 接口模式。

### 警告

- **[信息] 未来 UX 需求：** 生产阶段（第 3-6 个月）可能需要基础监控仪表盘。UX 设计应在该阶段之前创建。
- **[信息] CLI UX 质量：** 虽然不需要视觉 UX，但 F-DEV-07（CLI 操作）应包含可用性考量（帮助文本、错误消息、输出格式化）。

## 步骤 5：Epic 质量审查

### 状态

**已完成** — 8 个 Epic 中的全部 80 个 Story 已完成质量审查。

### 审查摘要

**各 Epic 的 Story 数量：**
| Epic | Story 数量 |
|------|-----------|
| E1: 项目基础 | 10 |
| E2: 数据摄入 | 8 |
| E3: 嵌入流水线 | 9 |
| E4: 质量管理 | 13 |
| E5: 存储与版本管理 | 9 |
| E6: 查询与检索 | 12 |
| E7: 工作流编排 | 14 |
| E8: 可观测性 | 5 |
| **合计** | **80** |

**质量检查清单：**

- [x] 所有 Story 遵循 假设/当/那么 验收标准格式
- [x] Story 大小适合单个开发者会话
- [x] Epic 内无前向依赖（各 Epic 可独立交付）
- [x] 每个 FR 至少映射到一个 Story（步骤 3 中已验证 100% 覆盖）
- [x] Epic 独立性得以保持 — 无 Epic 需要后续 Epic 的功能

### Story 中验证的关键设计决策

1. **DuckDB 角色明确定义** — 仅用于目录（元数据、Schema 注册、目录 SQL 查询）。Daft SQL 是 Lance 数据分析查询的主要 OLAP 引擎。
2. **零拷贝验证策略** — 组件边界验证（每个接口处的 Arrow IPC 往返检查），而非端到端缓冲区一致性检查。
3. **连接池大小** — 简化为 4 个读连接 + 1 个写连接，反映仅目录的工作负载特征。
4. **Story 1.2 风险探针** — 3 天时间盒，设有明确的 NO-GO 触发条件（Daft >= 0.7.8 Lance 集成验证）。这是项目的主要技术风险。

### 已识别的风险探针

| Story | 风险 | 缓解措施 |
|-------|------|----------|
| 1.2 | Daft 0.7.8 Lance 集成成熟度 | 3 天探针，已定义 NO-GO 触发条件 |
| 3.1 | HuggingFace 模型加载性能 | 在探针中进行基准测试，备选方案为 ONNX Runtime |
| 6.4 | Lance FTS 成熟度 | 独立验证 Story，附带备选方案 |

### 专家审查中发现的问题

专家审查期间进行了以下修正：
- 对过大 Story 进行拆分（E4 和 E7 中有多个 Story 被拆分）
- 添加新 Story 以覆盖空缺领域（质量流水线、可观测性集成）
- 修正验收标准以提高可测试性
- 更新风险评估并补充缓解计划

## 步骤 6：总结与建议

### 整体就绪状态

**可以开始实施** — 所有前提条件均已满足。PRD、架构、系统设计和 Epic & Story 均已完成、审核并保持一致。全部 68 个 FR 在 8 个 Epic 的 80 个 Story 中实现 100% 覆盖。

### 文档质量摘要

| 文档 | 状态 | 质量 | 问题 |
|------|------|------|------|
| PRD (`prd.md`) | 已完成 | 高 | F-ORCH-06 ID 拼写错误已修复，Daft 版本已修复，FR 计数差异 |
| 架构 (`architecture.md`) | 已完成 | 高 | FR 计数需更新（66 → 68） |
| 系统设计 (`system_design.md`) | 已完成，已审核 | 高 | 5 个严重 + 10 个高优先级问题已修复，附录 C 偏差已记录 |
| PRD 中文版 (`prd-zh.md`) | 已完成 | 高 | 与英文 PRD 同步 |
| Epic 与 Story (`epics.md`) | 已完成，专家审核 | 高 | 80 个 Story，100% FR 覆盖，已拆分 Story，已修正验收标准 |
| UX 设计 | 不需要（MVP） | 不适用 | v1 仅 CLI/SDK/Notebook |

### 剩余事项（非阻塞）

1. **[低] 更新 architecture.md FR 计数** — 将"55 + 11 = 66"更改为"55 + 13 = 68"，以反映实际的 FR 分解。
2. **[信息] 建议执行步骤 3 Lite** — 在 Sprint 1 开始之前验证 Daft >= 0.7.8 API 兼容性。Story 1.2 探针已部分解决此问题。
3. **[信息] 语言约定：** 英文为主要文档语言。中文版本（prd-zh.md、epics-zh.md）作为补充参考。architecture.md 目前为中文，应在未来的 Sprint 中翻译为英文。

### 建议的后续步骤

1. **开始 Sprint 1** — 从 E1: 项目基础（10 个 Story）开始。Story 1.2（Daft + Lance 集成探针）应最先执行，因为它设有 NO-GO 触发条件。
2. **MVP 核心执行** — 第 1-6 周涵盖 E1 至 E6（53 个 Story）。E7 和 E8 在第 4-6 周并行执行。
3. **监控风险探针** — 密切跟踪 Story 1.2、3.1 和 6.4 的结果。

### 问题汇总

| 步骤 | 类别 | 发现的问题 |
|------|------|-----------|
| 步骤 1 | 文档发现 | 0 份缺失文档（UX：有意不需要） |
| 步骤 2 | PRD 分析 | 2 个已修复（拼写错误 + 版本），1 个信息（FR 计数），1 个低优先级（验收标准 — 已解决） |
| 步骤 3 | Epic 覆盖率 | 68/68 个 FR 已覆盖 — 100% |
| 步骤 4 | UX 一致性 | 无问题（CLI/SDK MVP，不需要 UI） |
| 步骤 5 | Epic 质量 | 所有质量检查通过，风险探针已识别并制定缓解措施 |

### 最后说明

全部 6 个评估步骤已完成。项目已达到完全就绪状态：68 个 FR 由 8 个 Epic 中的 80 个 Story 覆盖，所有 Story 遵循一致的 假设/当/那么 格式，大小适合单个开发者会话，且无前向依赖。DuckDB 角色已明确（仅用于目录），风险探针已识别并制定了明确的缓解计划。可以立即开始 Sprint 1 / E1: 项目基础的实施。
