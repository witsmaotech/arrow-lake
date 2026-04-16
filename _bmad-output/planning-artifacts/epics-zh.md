---
stepsCompleted: [step-01-prerequisites, step-02-design-epics, step-03-create-stories, step-04-final-validation]
inputDocuments:
  - _bmad-output/planning-artifacts/prd.md
  - _bmad-output/planning-artifacts/architecture.md
  - _bmad-output/planning-artifacts/system_design.md
  - _bmad-output/planning-artifacts/implementation-readiness-report-2026-04-11.md
project_name: arrow-lake
date: 2026-04-11
total_frs: 68
total_nfrs: 32
---

# Arrow Lake - Epic 拆解

## 概述

本文档提供 Arrow Lake 的完整 Epic 和 Story 拆解，将 PRD、架构和系统设计中的需求分解为可实现的 Story。

## 需求清单

### 功能需求

#### 6.1 数据摄入（9 个 FR）

FR-ING-01: 从本地 FS、S3/MinIO、HTTP 摄入 text/CSV/JSON/Parquet 文件（P0）
FR-ING-02: 摄入图像（JPEG/PNG/WebP），自动生成缩略图（P0）
FR-ING-03: 摄入视频：在场景边界提取关键帧（PyAV），MVP 范围：每个场景提取单个关键帧（P1）
FR-ING-04: 摄入时计算文本嵌入（HuggingFace 本地 / Ray Serve / 外部 API）（P0）
FR-ING-05: 摄入时计算图像嵌入（CLIP/SigLIP）（P0）
FR-ING-06: 将原始数据 + 嵌入存储在统一的 Lance 表中（P0）
FR-ING-07: 嵌入计算完成后异步构建向量索引（P0）
FR-ING-08: 基于内容寻址的去重（SHA-256 精确匹配 + pHash 感知哈希）（P0）
FR-ING-09: 多保真度存储（缩略图 + 预览 + 原始）（P1）

#### 6.2 数据处理（9 个 FR）

FR-PROC-01: Daft DataFrame API 用于多模态数据转换（P0）
FR-PROC-02: GPU/CPU 异构调度（`use_gpu=True`）（P0）
FR-PROC-03: SQL 查询支持（Daft SQL + DuckDB）（P1）
FR-PROC-04: 质量评分流水线（NeMo Curator：去重、分类器、美学评分）（P1）
FR-PROC-05: 质量分数作为 Lance 列，支持下推谓词（P0）
FR-PROC-06: 图像/视频的惰性下载 + 解码（无需完整文件下载直到需要）（P0）
FR-PROC-07: Schema 迁移：添加/修改/删除列，无需全量重写（P0）
FR-PROC-08: 通过 Ray 进行分布式处理（foreach + AutoScale）（P0）
FR-PROC-09: 远程数据加载器模式（CPU 解码 -> Object Store -> GPU 训练）（P1）

#### 6.3 存储与版本管理（8 个 FR）

FR-STOR-01: 所有存储数据使用 Lance 格式，支持 Arrow 原生 I/O（P0）
FR-STOR-02: 每次写入自动版本管理（Lance 版本）（P0）
FR-STOR-03: 为重要版本创建命名标签（实验快照、生产）（P0）
FR-STOR-04: 时间旅行查询：读取任意历史版本（P0）
FR-STOR-05: 版本差异：比较两个版本（schema + 行 + 列变更）（P1）
FR-STOR-06: 压缩：合并 Fragment 文件，回收已删除列的空间（P0）
FR-STOR-07: 自动分层 Blob 生命周期管理（Standard -> IA -> Glacier）（P2）
FR-STOR-08: S3/MinIO 后端，可配置端点（P0）

#### 6.4 查询与检索（8 个 FR）

FR-QRY-01: 向量搜索（<1M 行使用 HNSW，1M+ 使用 IVF_PQ）（P0）
FR-QRY-02: 全文搜索（Lance FTS）（P0）
FR-QRY-03: 混合搜索（向量 + 文本，可配置 alpha 权重）（P0）
FR-QRY-04: OLAP 分析（Daft SQL 主查询，DuckDB 作为目录回退，支持 Lance 谓词下推）（P0）
FR-QRY-05: 流式结果（fetch_record_batch_reader，恒定内存）（P0）
FR-QRY-06: 分面搜索（DuckDB CUBE + 向量搜索）（P2）
FR-QRY-07: 基于数据量和查询模式的自适应索引选择（P0）
FR-QRY-08: 多模型集成搜索（连接多个嵌入列的搜索结果）（P2）

#### 6.5 目录与元数据（5 个 FR）

FR-CAT-01: 作为 Ray Named Actor 的集中式目录（内嵌 DuckDB）（P0）
FR-CAT-02: 注册数据集，包含 schema、列元数据和统计信息（P0）
FR-CAT-03: 通过 SQL 查询目录元数据（P0）
FR-CAT-04: 通过目录路由的统一搜索 API（P0）
FR-CAT-05: 作为 SQL 查询的 Lance 事件日志的数据血缘（P2）

#### 6.6 工作流编排（11 个 FR）

FR-ORCH-01: 所有批处理流水线使用 Metaflow FlowSpec（P0）
FR-ORCH-02: 本地执行：`python flow.py run`（P0）
FR-ORCH-03: 集群执行：`python flow.py run --with ray`（P0）
FR-ORCH-04: 生产部署：`python flow.py --with ray argo-workflows create`（P1）
FR-ORCH-05a: 瞬态重试：@retry 指数退避，用于 Spot Worker 抢占和网络错误（P0）
FR-ORCH-05b: 错误分类：@catch 处理器将错误分类为可重试 vs 致命（P0）
FR-ORCH-05c: 状态回滚：致命错误时 Lance 版本回退到上次已知良好状态（P0）
FR-ORCH-06: 定时流水线：@schedule(daily/hourly/cron)（P0）
FR-ORCH-07: 基于标签的运行跟踪和恢复（P1）
FR-ORCH-08: 弹性突发：按需自动扩展 GPU Worker，空闲时缩回（P1）
FR-ORCH-09: 事件溯源：Lance 版本 + Metaflow 标签 = 不可变审计追踪（P2）

#### 6.7 开发者体验（7 个 FR）

FR-DEV-01: 一键启动平台：`docker compose up -d`（P1）
FR-DEV-02: Jupyter Notebook 集成用于数据探索（P1）
FR-DEV-03: 使用 uv 进行依赖管理（替代 Poetry）（P0）
FR-DEV-04: Python SDK：`from arrow_lake import Lake`（P0）
FR-DEV-05: 数据测试：对 Lance/Daft/DuckDB 结果的 pytest 断言（P1）
FR-DEV-06: 渐进式复杂度：5 个 API 级别（function -> Daft -> SQL -> Ray -> Metaflow）（P0）
FR-DEV-07: CLI 用于常用操作（ingest、search、status、version）（P2）

#### 6.8 质量管理 - ADR-02 衍生（5 个 FR）

FR-QUA-01: QualityFilter 注册：可插拔的行级过滤器接口（P0）
FR-QUA-02: 内置过滤器：TextLengthFilter + ImageResolutionFilter（P0）
FR-QUA-03: 死信持久化：被拒绝的行 -> `{table}_dead_letter` Lance 表（P0）
FR-QUA-04: 质量统计报告：总计/通过/拒绝 + 按过滤器的详细分布（P0）
FR-QUA-05: Schema 验证关卡：严格模式拒绝未知列/类型不匹配（P0）

#### 6.9 可观测性 - ADR-02 衍生（6 个 FR）

FR-OBS-01: Prometheus `/metrics` HTTP 端点（Prometheus 格式）（P0）
FR-OBS-02: 摄入指标：每个表的行数/字节数/持续时间/错误数（P0）
FR-OBS-03: 处理指标：嵌入数/质量拒绝数/活跃任务数（P0）
FR-OBS-04: 查询指标：每种 query_type 的查询数/延迟/结果数（P0）
FR-OBS-05: 系统指标：Ray Actor 数/表数/运行时间（P0）
FR-OBS-06: 指标可配置：端口/路径的环境变量，支持禁用（P0）

### 非功能需求

#### 7.1 性能（6 个 NFR）

NFR-PERF-01: 向量搜索延迟（10M 行，top_k=100）< 10ms
NFR-PERF-02: 摄入吞吐量（文本，单节点）> 50K 行/秒
NFR-PERF-03: 全链路 Arrow 零拷贝利用率 > 90%
NFR-PERF-04: 惰性求值在 1% 选择率下的加速 > 100x vs 即时求值
NFR-PERF-05: 流式查询内存占用（100M 行）< 100MB
NFR-PERF-06: PyTorch DataLoader 零拷贝 + 异步 GPU 传输（pin_memory + non_blocking）

#### 7.2 可靠性（4 个 NFR）

NFR-REL-01: 工作流恢复率（无人工干预）> 90%（MVP），> 95%（生产）
NFR-REL-02: 故障时数据完整性（Lance 版本 + Metaflow 检查点）零数据丢失
NFR-REL-03: Catalog Actor 可用性 max_restarts=3，自动恢复
NFR-REL-04: 瞬态故障的 MTTR < 10 分钟

#### 7.3 可扩展性（5 个 NFR）

NFR-SCALE-01: 数据量支持（单节点）最大 10M 行
NFR-SCALE-02: 数据量支持（分布式）最大 1B 行
NFR-SCALE-03: 并发查询支持最大 100 QPS（含读副本）
NFR-SCALE-04: GPU 扩展模型分数 GPU（0.5），最多 8 个 Worker
NFR-SCALE-05: 弹性突发：0 到 8 个 GPU Worker，扩展时间 < 5 分钟

#### 7.4 成本效率（4 个 NFR）

NFR-COST-01: 弹性突发月度成本（100GB/月处理量）< $500/月
NFR-COST-02: 通过自动分层降低存储成本（100TB）比全 Standard > 50%
NFR-COST-03: 突发工作负载的 Spot GPU 利用率 > 70% spot（可用时）
NFR-COST-04: 基线（空闲）平台成本 < $400/月

#### 7.5 易用性（4 个 NFR）

NFR-USE-01: 开发者上手时间 < 30 分钟
NFR-USE-02: 从本地到生产部署的代码变更量为零
NFR-USE-03: 嵌入模型热切换零数据重写，零停机
NFR-USE-04: API 复杂度级别 5 个级别（简单 -> 高级）

#### 7.6 安全性（4 个 NFR）

NFR-SEC-01: 密钥管理 环境变量 / .env 文件，无硬编码凭证
NFR-SEC-02: S3/MinIO 访问控制 IAM 角色（生产）/ 访问密钥（开发）
NFR-SEC-03: API 边界的输入验证 摄入时的 Schema 验证
NFR-SEC-04: 容器安全 官方基础镜像，最小攻击面

#### 7.7 可观测性（5 个 NFR）

NFR-OBS-01: 流水线指标 Prometheus + Grafana 仪表板
NFR-OBS-02: Ray 集群监控 Ray Dashboard（内置）
NFR-OBS-03: 结构化日志 JSON 日志，含关联 ID
NFR-OBS-04: 数据质量报告 Metaflow Cards（每个步骤的 HTML 报告）
NFR-OBS-05: 每次流水线运行的成本跟踪 Ray 资源注解 + Prometheus

### 附加需求

#### 项目设置与依赖

AR-01: 使用 uv 初始化全新项目进行依赖管理，包含 pyproject.toml 和 uv.lock 文件
AR-02: 修正 .python-version 文件中的 Python 版本
AR-03: 配置 Ruff 进行代码检查和格式化，使用 ruff.toml
AR-04: 配置 MyPy 进行类型检查，使用 mypy.ini
AR-05: 在 .pre-commit-config.yaml 中设置 pre-commit 钩子
AR-06: 在 pyproject.toml 中定义辅助库版本（structlog、tenacity、pydantic、boto3、prometheus-client）
AR-07: 实现前验证 Daft >= 0.7.8 + DuckDB Lance 扩展 + Pydantic v2 Arrow 类型映射

#### 基础设施与部署

AR-08: 创建 Dockerfile 用于容器化
AR-09: 创建 docker-compose.yml 用于本地开发，包含 Ray Head + 1 个 Worker（CPU，可选 GPU）
AR-10: 创建 docker-compose.gpu.yml overlay 用于 GPU 支持
AR-11: 创建 prometheus.yml 配置用于监控
AR-12: 在 docker-compose 配置中配置 Prometheus 和 Grafana
AR-13: 使用官方 Ray Helm Chart 创建 K8s 生产部署的 Helm Chart，包含自定义 values
AR-14: 创建 Helm 模板：deployment.yaml、service.yaml、networkpolicy.yaml、prometheusrule.yaml
AR-15: 在 Helm Chart 模板中定义 NetworkPolicy，但在 values.yaml 中默认禁用
AR-16: 配置 values.yaml 和 values-dev.yaml 用于 Helm 部署

#### 配置管理

AR-17: 实现 Pydantic Settings，支持 4 层覆盖：代码默认值 -> .env 文件 -> 环境变量 -> Metaflow Config YAML
AR-18: 创建 .env.example 模板文件，包含占位符值（通过 .gitignore 排除）
AR-19: 创建 YAML 配置文件：configs/dev.yaml、configs/staging.yaml、configs/prod.yaml
AR-20: 在启动时实现必需配置字段的快速失败验证
AR-21: 配置 Metaflow @project 装饰器和 Config YAML 注入

#### 安全

AR-22: 实现本地开发的 Docker 网络隔离（bridge 网络，暴露端口 8000 用于指标，8265 用于 Ray Dashboard）
AR-23: 为 Docker Compose（自签名）和 K8s（cert-manager）配置 TLS
AR-24: 确保 AWS GP3 EBS 加密用于静态存储
AR-25: 配置 Prometheus 服务发现以限制 /metrics 端点访问

#### 监控与日志

AR-26: 使用 structlog 实现结构化 JSON 日志
AR-27: 在所有日志中包含 correlation_id（从 Metaflow run_id 映射）
AR-28: 暴露 /metrics HTTP 端点，使用 Prometheus 格式
AR-29: 实现 17 个 Prometheus 指标，遵循命名模式：arrow_lake_{domain}_{metric}_{unit}
AR-30: 创建 metrics.py 用于 Prometheus 指标注册和定义
AR-31: 在 config.py 中为所有模块配置 structlog

#### 集成需求

AR-32: 通过 boto3 在 arrow_lake/ingest/sources/s3.py 中实现 S3/MinIO 集成
AR-33: 在 arrow_lake/ingest/sources/local.py 中实现本地文件系统数据源
AR-34: 通过 prometheus_client 库集成 Prometheus
AR-35: 通过 @project 装饰器和 Config YAML 配置 Metaflow 集成

#### 测试需求

AR-36: 组织三级测试：tests/unit/、tests/integration/、tests/e2e/
AR-37: 在 tests/integration/test_boundary_*.py 中创建 6 个 Arrow 零拷贝边界测试
AR-38: 配置 CI 流水线的 PR 门禁：Ruff 检查 + MyPy 类型检查 + pytest（仅 CPU）
AR-39: 配置夜间 GPU 测试运行，自动调度
AR-40: 创建 conftest.py 用于共享测试 fixtures
AR-41: 由于成本原因，GPU 测试与 CPU 测试分开实现

#### 代码结构

AR-42: 创建 arrow_lake/ 包，包含子模块：catalog/、ingest/、quality/、embedding/、query/、ray_runtime/、sdk/
AR-43: 在主包外创建 flows/ 包，包含 Metaflow Flow 定义
AR-44: 在 arrow_lake/exceptions.py 中实现自定义异常层次结构
AR-45: 为所有 Schema 定义 Pydantic v2 模型，使用正确的类型注解
AR-46: 实现带正确类型映射的 Pydantic 到 Arrow Schema 的转换

#### CI/CD

AR-47: 创建 .github/workflows/ci.yml 用于 PR 门禁控制
AR-48: 创建 .github/workflows/gpu-tests.yml 用于夜间和手动 GPU 测试
AR-49: 创建 .github/workflows/release.yml 用于标签触发的发布

### UX 设计需求

不适用 - MVP 仅限 CLI/SDK/Notebook，无需前端 UI。

### FR 覆盖映射

| FR ID | Epic | 简要描述 |
|-------|------|----------|
| FR-ING-01 | Epic 3 | 从本地 FS、S3/MinIO、HTTP 摄入 text/CSV/JSON/Parquet |
| FR-ING-02 | Epic 3 | 摄入图像（JPEG/PNG/WebP），自动生成缩略图 |
| FR-ING-03 | Epic 3 | 摄入视频：在场景边界提取关键帧（PyAV），MVP：每个场景单个关键帧 |
| FR-ING-04 | Epic 4 | 摄入时计算文本嵌入（HuggingFace 本地 / Ray Serve / 外部 API） |
| FR-ING-05 | Epic 4 | 摄入时计算图像嵌入（CLIP/SigLIP） |
| FR-ING-06 | Epic 3 | 将原始数据 + 嵌入存储在统一的 Lance 表中 |
| FR-ING-07 | Epic 4 | 嵌入计算完成后异步构建向量索引 |
| FR-ING-08 | Epic 4 | 基于内容寻址的去重（SHA-256 精确匹配 + pHash 感知哈希） |
| FR-ING-09 | Epic 3 | 多保真度存储（缩略图 + 预览 + 原始） |
| FR-PROC-01 | Epic 3 | Daft DataFrame API 用于多模态数据转换 |
| FR-PROC-02 | Epic 4 | GPU/CPU 异构调度（use_gpu=True） |
| FR-PROC-03 | Epic 7 | SQL 查询支持（Daft SQL + DuckDB） |
| FR-PROC-04 | Epic 8 | 质量评分流水线（NeMo Curator：去重、分类器、美学评分） |
| FR-PROC-05 | Epic 4 | 质量分数作为 Lance 列，支持下推谓词 |
| FR-PROC-06 | Epic 3 | 图像/视频的惰性下载 + 解码 |
| FR-PROC-07 | Epic 2 | Schema 迁移：添加/修改/删除列，无需全量重写 |
| FR-PROC-08 | Epic 6 | 通过 Ray 进行分布式处理（foreach + AutoScale） |
| FR-PROC-09 | Epic 6 | 远程数据加载器模式（CPU 解码 -> Object Store -> GPU 训练） |
| FR-STOR-01 | Epic 1 | 所有存储数据使用 Lance 格式，支持 Arrow 原生 I/O |
| FR-STOR-02 | Epic 2 | 每次写入自动版本管理（Lance 版本） |
| FR-STOR-03 | Epic 2 | 为重要版本创建命名标签 |
| FR-STOR-04 | Epic 2 | 时间旅行查询：读取任意历史版本 |
| FR-STOR-05 | Epic 2 | 版本差异：比较两个版本 |
| FR-STOR-06 | Epic 2 | 压缩：合并 Fragment 文件，回收空间 |
| FR-STOR-07 | Epic 7 | 自动分层 Blob 生命周期管理（Standard -> IA -> Glacier） |
| FR-STOR-08 | Epic 1 | S3/MinIO 后端，可配置端点 |
| FR-QRY-01 | Epic 5 | 向量搜索（<1M 使用 HNSW，1M+ 使用 IVF_PQ） |
| FR-QRY-02 | Epic 5 | 全文搜索（Lance FTS） |
| FR-QRY-03 | Epic 5 | 混合搜索（向量 + 文本，可配置 alpha） |
| FR-QRY-04 | Epic 5 | OLAP 分析（Daft SQL 主查询，DuckDB 作为目录回退） |
| FR-QRY-05 | Epic 5 | 流式结果（fetch_record_batch_reader，恒定内存） |
| FR-QRY-06 | Epic 8 | 分面搜索（DuckDB CUBE + 向量搜索） |
| FR-QRY-07 | Epic 5 | 基于数据量和查询模式的自适应索引选择 |
| FR-QRY-08 | Epic 8 | 多模型集成搜索 |
| FR-CAT-01 | Epic 1 | 作为 Ray Named Actor 的集中式目录（内嵌 DuckDB） |
| FR-CAT-02 | Epic 1、Epic 2 | 注册数据集，包含 schema、列元数据和统计信息（Epic 1：初始注册；Epic 2：通过版本管理进行生命周期管理） |
| FR-CAT-03 | Epic 5 | 通过 SQL 查询目录元数据 |
| FR-CAT-04 | Epic 5 | 通过目录路由的统一搜索 API |
| FR-CAT-05 | Epic 8 | 作为 SQL 查询的 Lance 事件日志的数据血缘 |
| FR-ORCH-01 | Epic 6 | 所有批处理流水线使用 Metaflow FlowSpec |
| FR-ORCH-02 | Epic 6 | 本地执行：python flow.py run |
| FR-ORCH-03 | Epic 6 | 集群执行：python flow.py run --with ray |
| FR-ORCH-04 | Epic 7 | 生产部署：argo-workflows create |
| FR-ORCH-05a | Epic 6 | 瞬态重试：@retry 指数退避 |
| FR-ORCH-05b | Epic 6 | 错误分类：@catch 处理器 |
| FR-ORCH-05c | Epic 6 | 致命错误时状态回滚：Lance 版本回退 |
| FR-ORCH-06 | Epic 6 | 定时流水线：@schedule |
| FR-ORCH-07 | Epic 6 | 基于标签的运行跟踪和恢复 |
| FR-ORCH-08 | Epic 7 | 弹性突发：自动扩展 GPU Worker |
| FR-ORCH-09 | Epic 8 | 事件溯源：Lance 版本 + Metaflow 标签 = 审计追踪 |
| FR-DEV-01 | Epic 1 | 一键启动平台：docker compose up -d |
| FR-DEV-02 | Epic 7 | Jupyter Notebook 集成 |
| FR-DEV-03 | Epic 1 | 使用 uv 进行依赖管理 |
| FR-DEV-04 | Epic 1 | Python SDK：from arrow_lake import Lake（初始化） |
| FR-DEV-05 | Epic 2 | 数据测试：对 Lance/Daft/DuckDB 结果的 pytest 断言 |
| FR-DEV-06 | Epic 1 | 渐进式复杂度：5 个 API 级别（L1-2 在 Epic 1，迭代演进） |
| FR-DEV-07 | Epic 7 | CLI 用于常用操作 |
| FR-QUA-01 | Epic 4 | QualityFilter 注册：可插拔的行级过滤器接口 |
| FR-QUA-02 | Epic 4 | 内置过滤器：TextLengthFilter + ImageResolutionFilter |
| FR-QUA-03 | Epic 4 | 死信持久化：被拒绝的行 -> dead_letter Lance 表 |
| FR-QUA-04 | Epic 4 | 质量统计报告：总计/通过/拒绝 |
| FR-QUA-05 | Epic 4 | Schema 验证关卡：严格模式拒绝未知列 |
| FR-OBS-01 | Epic 7 | Prometheus /metrics HTTP 端点 |
| FR-OBS-02 | Epic 7 | 摄入指标：每个表的行数/字节数/持续时间/错误数 |
| FR-OBS-03 | Epic 7 | 处理指标：嵌入数/质量拒绝数/活跃任务数 |
| FR-OBS-04 | Epic 7 | 查询指标：每种 query_type 的查询数/延迟/结果数 |
| FR-OBS-05 | Epic 7 | 系统指标：Ray Actor 数/表数/运行时间 |
| FR-OBS-06 | Epic 7 | 指标可配置：端口/路径的环境变量，支持禁用 |

## Epic 列表

### Epic 1: 平台引导
**用户价值：** Maya 可以通过 `docker compose up -d` 启动平台，创建 Lance 数据集，在 Catalog 中注册，并看到基本指标和结构化日志正常输出。

**子阶段：**
- 1A: 项目骨架（pyproject.toml、uv、Ruff、MyPy、pre-commit）— 配置验证测试
- 1B: 配置与设置（Pydantic Settings 4 层、.env.example、快速失败）— 纯业务逻辑 TDD
- 1C: 平台启动（Dockerfile、docker-compose.yml、DuckDB 连接池、Catalog Actor、可观测性脚手架）— 集成测试

**覆盖的 FR：** FR-DEV-01、FR-DEV-03、FR-DEV-04（SDK 初始化）、FR-DEV-06（L1-2）、FR-STOR-01、FR-STOR-08、FR-CAT-01、FR-CAT-02（8 个 FR）

**覆盖的 AR：** AR-01~06、AR-07（Step 3 Lite Spike）、AR-08、AR-09、AR-17~20、AR-26、AR-27、AR-31、AR-42~46、Arrow 版本锁定、DuckDB WAL 连接池、DI 协议边界、测试基础设施（fixtures/factories/mocks）、平台启动冒烟测试（约 26 个 AR）

**风险 Spike：** DuckDB Lance 扩展生产验证、Daft >= 0.7.8 Arrow 类型映射验证

**NFR 验证：** NFR-USE-01（TTV < 30 分钟作为 Epic 验收门）

**门禁：** docker compose up -d -> from arrow_lake import Lake -> 创建数据集 -> 注册 Catalog -> /metrics 可访问 -> 结构化日志正常输出

**MVP：** 核心（第 1-2 周）

### Epic 2: 数据版本管理
**用户价值：** Maya 可以标记数据集版本、时间旅行到任意历史状态、并排比较版本、压缩存储、演进 Schema，并使用 pytest 验证数据正确性。

**覆盖的 FR：** FR-STOR-02~06、FR-PROC-07、FR-DEV-05、FR-CAT-02（8 个 FR）

**覆盖的 AR：** Schema 演进策略、Arrow 边界验证测试、优雅降级规范、fixture 数据版本管理（约 5 个 AR）

**NFR 验证：** NFR-STOR（版本完整性、零数据丢失）、NFR-REL-02（故障时数据完整性）

**MVP：** 核心（第 2-3 周）

### Epic 3: 多模态摄入
**用户价值：** Maya 可以从本地 FS、S3 或 HTTP 摄入文本、图像和视频到统一的 Lance 表中，支持惰性 Blob 加载和自动缩略图生成。

**覆盖的 FR：** FR-ING-01~03、FR-ING-06、FR-ING-09、FR-PROC-01、FR-PROC-06（7 个 FR）

**覆盖的 AR：** AR-32（S3/boto3）、AR-33（本地 FS）、错误代码分类体系（ErrorCode 枚举）（约 4 个 AR）

**NFR 验证：** NFR-PERF-02（摄入吞吐量 > 50K 行/秒）

**可选（John 的建议）：** 最小元数据搜索（文件名/日期过滤），以减少 Epic 5 之前的价值空白

**MVP：** 核心（第 3-4 周）

### Epic 4: 嵌入与质量
**用户价值：** Maya 可以在摄入时计算嵌入、应用可插拔的质量过滤器、去重内容，并将被拒绝的行持久化到死信表。

**覆盖的 FR：** FR-ING-04、FR-ING-05、FR-ING-07、FR-ING-08、FR-PROC-02、FR-PROC-05、FR-QUA-01~05（11 个 FR）

**风险 Spike：** NeMo Curator CPU 回退验证（高概率：NVIDIA 专属依赖）

**NFR 验证：** NFR-PERF-06（GPU 零拷贝 + pin_memory）、NFR-SEC-03（API 边界的输入验证）

**MVP：** 核心（第 4-5 周）

### Epic 5: 语义搜索与分析
**用户价值：** Raj 可以执行向量搜索、全文搜索、混合 RRF 搜索和 OLAP SQL 分析，支持流式结果和自适应索引选择。

**覆盖的 FR：** FR-QRY-01~05、FR-QRY-07、FR-CAT-03、FR-CAT-04（8 个 FR）

**覆盖的 AR：** 索引构建时间预算、性能基准测试套件（约 3 个 AR）

**NFR 验证：** NFR-PERF-01（< 10ms 向量搜索）、NFR-PERF-04（100x 惰性求值加速）、NFR-PERF-05（流式 < 100MB）

**MVP 核心路径端点** — Raj 的"顿悟时刻"

**MVP：** 核心（第 5-6 周）

### Epic 6: 流水线编排与集成
**用户价值：** Maya 可以使用 Metaflow 定义自动化数据流水线，具备三级自愈能力（重试/分类/回滚）、定时执行和基于标签的运行跟踪。

**集成 Story：** Maya E2E 流水线 — 1000 条混合质量记录，4 个步骤（ingest -> quality -> embed -> search），< 45 分钟，TTV + /metrics 可观测。

**覆盖的 FR：** FR-ORCH-01~03、FR-ORCH-05a~c、FR-ORCH-06、FR-ORCH-07、FR-PROC-08、FR-PROC-09（10 个 FR）

**NFR 验证：** NFR-REL-01~04（可靠性）、NFR-SCALE-01（单节点 10M 行）

**MVP：** 增强（第 6-8 周）

### Epic 7: 生产与可观测性
**用户价值：** Sam 可以通过 Helm 部署到 K8s、利用弹性 GPU 突发扩展、通过 Prometheus/Grafana 仪表板监控，并通过 CLI 管理平台。

**覆盖的 FR：** FR-DEV-02、FR-DEV-07、FR-ORCH-04、FR-ORCH-08、FR-PROC-03、FR-STOR-07、FR-OBS-01~06（12 个 FR）

**覆盖的 AR：** AR-10~16、AR-22~25、AR-28~30、AR-47~49（约 13 个 AR）

**NFR 验证：** NFR-COST-01~04（成本）、NFR-SCALE-02~05（可扩展性）、NFR-OBS-01~05（可观测性）

**MVP：** 生产（第 3-4 个月：部署+可观测性，第 4-6 个月：扩展+安全）

### Epic 8: 高级功能
**用户价值：** 高级用户可以执行分面搜索、多模型集成搜索、数据血缘追踪、事件溯源审计和 NeMo Curator GPU 加速质量评分。

**覆盖的 FR：** FR-QRY-06、FR-QRY-08、FR-CAT-05、FR-ORCH-09、FR-PROC-04（5 个 FR）

**MVP：** 扩展（第 6-12 个月）

---

### 依赖链

```
Epic 1（第 1-2 周）→ Epic 2（第 2-3 周）┐
                                         ├→ Epic 3（第 3-4 周）→ Epic 4（第 4-5 周）→ Epic 5（第 5-6 周）
                                         │                                    │
                                         └─（并行）                           ├→ Epic 6 E2E（第 6-8 周）
                                                                              │
                                         └─────────────────────────────────────┘
                                                                              │
                              Epic 7（第 3-6 个月）←──────────────────────────┘
                              Epic 8（第 6-12 个月）←─────────────────────────┘
```

### MVP 分层范围

| 层级 | Epic | FR 数量 | 目标 | 门禁标准 |
|------|------|---------|------|----------|
| MVP 核心 | 1-5（最小路径） | ~18 | 第 1-6 周 | Raj 可以使用嵌入进行搜索 |
| MVP 增强 | + 2-3（完整）+ 6（E2E） | ~30 | 第 6-8 周 | Maya E2E：1000 条记录，4 个步骤，<45 分钟 |
| 生产 | + 6（完整）+ 7 | ~50 | 第 3-6 个月 | Sam 部署到 K8s，弹性突发正常工作 |
| 扩展 | + 8 | 68 | 第 6-12 个月 | 完整功能集 |

### 风险 Spike

| Spike | Epic | 风险等级 | 触发条件 | 缓解措施 |
|-------|------|----------|----------|----------|
| DuckDB Lance 扩展验证 | Epic 1 | P0 | 如果失败则阻塞 Epic 1C | 评估替代查询路径 |
| Daft >= 0.7.8 Arrow 兼容性 | Epic 1 | P0 | 如果失败则阻塞 Epic 1A/B | 锁定 Arrow 版本矩阵 |
| NeMo Curator CPU 回退 | Epic 4 | 高 | Epic 4 嵌入 Story | 实现基于 CPU 的质量评分回退 |
| Metaflow + Ray 集成 | Epic 6 | 中 | 如果 `--with ray` 失败则阻塞 Epic 6 编排 Story | 在 Story 6.1 中验证；回退到仅本地执行 |
| DuckDB 多连接 | Epic 1 | P0 | 如果并发读取失败则 Story 1.6 连接池设计无效 | 在 Story 1.2 Spike 中验证；回退到单连接顺序读取 |

---

## Epic 1: 平台引导

Maya 可以通过 `docker compose up -d` 启动平台，创建 Lance 数据集，在 Catalog 中注册，并看到基本指标和结构化日志正常输出。

**FR：** FR-DEV-01、FR-DEV-03、FR-DEV-04、FR-DEV-06、FR-STOR-01、FR-STOR-08、FR-CAT-01、FR-CAT-02

### Story 1.1: 项目骨架与工具链配置

作为一名开发者，
我希望有一个配置完善的 Python 项目，包含代码检查、格式化、类型检查和 pre-commit 钩子，
以便所有贡献者从第一天起就遵循一致的代码质量标准。

**验收标准：**

**假设** 有一个干净目录，`pyproject.toml` 定义了 uv workspace、Ruff、MyPy 和依赖项
**当** 我运行 `uv sync`
**那么** 所有依赖项安装成功，生成锁定的 `uv.lock`
**并且** `ruff check .` 在 `arrow_lake/` 包上以零错误通过
**并且** `mypy arrow_lake/` 以严格模式通过
**并且** `pre-commit run --all-files` 在干净克隆上通过
**并且** `.python-version` 指定了锁定的 Python 版本
**并且** `arrow_lake/` 包结构存在，包含子模块：`catalog/`、`ingest/`、`quality/`、`embedding/`、`query/`、`ray_runtime/`、`sdk/`
**并且** `flows/` 包存在于主包之外，用于 Metaflow Flow 定义
**并且** `.gitignore` 排除 `.env`、`__pycache__/`、`.venv/`、`*.egg-info/`
**并且** `uv sync` 在 MinIO 不可达或磁盘空间不足时给出可操作的错误信息并优雅失败
**并且** CI 管线（GitHub Actions）在每次推送到 `main` 和 PR 时运行 lint + 类型检查 + 单元测试（仅 CPU）——包括 Ruff check、MyPy 严格模式和 `pytest tests/unit/`；高级 CI（GPU 测试、Helm 验证）推迟至 Story 7.14

### Story 1.2: Spike — 技术兼容性验证

作为一名开发者，
我希望验证 Daft >= 0.7.8、DuckDB Lance 扩展和 Pydantic v2 Arrow 类型映射能协同工作，
以便在开始实现之前确认核心技术栈的可行性。

**验收标准：**

**时间盒：** 3 天（包括环境搭建、测试脚本、结果文档化）

**不通过触发条件（任一 = 不通过）：**
- DuckDB Lance 扩展无法查询 Lance 表（基本 `SELECT` 返回错误）
- Daft 无法将 Lance 数据集转换为 Arrow RecordBatch 而不报错
- Pydantic v2 `list_[float32]` 字段无法序列化为 Arrow Schema
- Arrow 缓冲区地址比较显示 Lance→Daft 边界存在静默数据拷贝（零拷贝验证失败）
- Metaflow `python flow.py run --with ray` 无法初始化 Ray 集群或提交任务（验证 Epic 6 关键依赖）

**不通过回退方案：**
- DuckDB 失败：切换到 Daft SQL 作为 OLAP 引擎（牺牲分析深度）或 DuckDB 作为纯目录存储，通过 Daft 进行 OLAP
- Daft 失败：锁定到最小可行 Daft 版本；如果不兼容，评估 Polars 作为 DataFrame 替代
- Pydantic 失败：使用手动 Arrow Schema 构建和显式类型映射
- Metaflow+Ray 失败：评估 `@ray.remote` 装饰器模式作为轻量替代；如不兼容，推迟 Metaflow 至 Sprint 5，使用纯 Ray 进行编排

**假设** 有一个全新的 Python 环境，安装了 `pip install daft>=0.7.8 duckdb lancedb pydantic>=2.0 pyarrow`
**当** 我运行兼容性测试脚本
**那么** Daft 可以读取 Lance 数据集并无错误地转换为 Arrow
**并且** DuckDB 可以通过 DuckDB Lance 扩展查询 Lance 表，执行 `SELECT * FROM lance_table LIMIT 10`
**并且** DuckDB Lance 扩展支持并发读连接（>=4 个同时读取器），返回正确的查询结果 — 验证 Story 1.6 连接池设计
**并且** Pydantic v2 模型的 `list_[float32](768)` 字段正确序列化为 Arrow Schema
**并且** pyarrow 版本在 `pyproject.toml` 中锁定到精确兼容版本（例如 `pyarrow==15.x.y`）——`>=` 范围约束不够；Spike 必须基于 Daft + Lance 兼容性测试产出固定 pin
**并且** 兼容性矩阵记录在 `docs/tech-compatibility.md` 中，列出已测试版本
**并且** 该 Spike 产出通过/不通过建议，记录在项目 README 中，每个验证项明确标注通过/不通过
**并且** 一个最小 Metaflow Flow（带 `@ray` 装饰器）可以通过 `python flow.py run --with ray` 运行，并向 Ray 提交基本任务 — 验证 Epic 6 关键依赖

### Story 1.3: 配置与设置层

作为一名开发者，
我希望有一个 4 层配置系统（代码默认值 -> .env 文件 -> 环境变量 -> Metaflow YAML），
以便平台配置在本地开发、预发布和生产环境中一致工作，无需代码变更。

**验收标准：**

**假设** 有一个 Pydantic Settings 模型 `ArrowLakeConfig`，包含存储、计算、可观测性和安全相关字段
**当** 我在不加载任何 `.env` 文件或环境变量的情况下加载配置
**那么** 应用合理的默认值（本地 MinIO、无 GPU、指标在端口 8000）
**当** 我创建一个包含 `S3_ENDPOINT=http://localhost:9000` 的 `.env` 文件
**那么** `.env` 文件的值覆盖代码默认值
**当** 我设置 `ARROW_LAKE__S3_ENDPOINT=http://staging-s3.internal:9000` 作为环境变量
**那么** 环境变量覆盖 `.env` 和代码默认值
**当** 加载 Metaflow Config YAML
**那么** YAML 值覆盖所有其他层
**并且** 快速失败验证在启动时拒绝缺失或无效的必需字段（例如 `STORAGE__BACKEND`）
**并且** `.env.example` 包含所有可配置字段，带占位符值和文档注释
**并且** `configs/dev.yaml`、`configs/staging.yaml`、`configs/prod.yaml` 模板文件存在

### Story 1.4: SDK 基础与异常层次结构

作为一名开发者，
我希望有一个最小的 Python SDK，包含清晰的入口点和自定义异常层次结构，
以便用户可以从一个干净的导入开始与 Arrow Lake 交互。

**验收标准：**

**假设** `arrow_lake` 包已安装
**当** 我运行 `from arrow_lake import Lake`
**那么** 导入成功无错误
**当** 我使用默认配置调用 `Lake()`
**那么** 创建一个连接到本地开发后端的 Lake 实例
**并且** `help(Lake)` 显示可用方法：`ingest()`、`search()`、`catalog()`、`version()`
**并且** 存在自定义异常层次结构：`ArrowLakeError`（基类）、`StorageError`、`QueryError`、`IngestError`、`ConfigurationError`、`ValidationError`
**并且** 所有异常包含结构化属性：`error_code`（枚举）、`message`、`context`（字典）
**并且** `tests/unit/test_exceptions.py` 验证所有异常类型可导入和可抛出

### Story 1.5: 可观测性脚手架

作为一名平台工程师，
我希望从第一天起就拥有带关联 ID 的结构化 JSON 日志和 Prometheus 指标注册表，
以便从一开始就能调试问题和跟踪所有组件的平台健康状况。

**验收标准：**

**假设** `structlog` 库已在 `arrow_lake.core.logging` 中配置
**当** 任何模块在 INFO 级别记录消息
**那么** 输出为结构化 JSON，包含键：`timestamp`、`level`、`module`、`message`、`correlation_id`
**并且** `correlation_id` 默认为 UUID，可通过环境变量 `ARROW_LAKE__CORRELATION_ID` 设置
**当** 指标模块在 `arrow_lake.core.metrics` 中初始化
**那么** 创建一个 Prometheus 注册表，命名模式为 `arrow_lake_{domain}_{metric}_{unit}`
**并且** Epic 1 定义了 3 个基本指标：`arrow_lake_system_uptime_seconds`（Gauge，标签：无）、`arrow_lake_catalog_tables_total`（Gauge，标签：无）、`arrow_lake_catalog_queries_total`（Counter，标签：query_type）
**并且** 剩余指标（摄入、查询、处理、质量、错误域）在各自功能 Epic（Epic 3-7）中随着每个功能的实现逐步引入
**并且** 每个指标定义包含：名称、类型（Counter/Histogram/Gauge）、标签 Schema 和描述
**并且** `tests/unit/test_metrics.py` 验证所有注册指标的类型、标签和命名约定正确
**并且** 指标可通过 `ARROW_LAKE__METRICS_ENABLED=false` 环境变量禁用
**并且** `arrow_lake.core.validation` 中提供 `ArrowCopyDetector` 工具，通过比较组件边界的 Arrow 缓冲区地址来验证零拷贝；如果检测到静默拷贝则记录 WARNING 并递增 `arrow_lake_zero_copy_violations_total`（Counter，labels: boundary）
**并且** `ArrowCopyDetector` 集成到 6 个标准边界测试（test_boundary_lance_daft、test_boundary_daft_duckdb 等）作为可复用的断言助手

### Story 1.6: DuckDB WAL 连接池

作为一名开发者，
我希望有一个自定义的 WAL 模式 DuckDB 连接池，可配置读写连接数，
以便 Catalog Actor 和查询操作可以共享 DuckDB 而不会出现写入饥饿或连接耗尽。

**验收标准：**

**假设** 有一个 `DuckDBConnectionPool` 类，初始化为 `read_connections=4, write_connections=1`（仅用于目录的连接池大小）
**当** 我获取一个写连接并执行 `CREATE TABLE test (id INT)`
**那么** 写入成功，使用后连接返回到连接池（上下文管理器）
**当** 我并发获取 4 个读连接并执行 `SELECT * FROM test`
**那么** 所有 4 个读取同时成功
**当** 我在 4 个连接都忙碌时尝试获取第 5 个读连接
**那么** 连接池阻塞直到有连接返回或超时（可配置超时）
**并且** `PoolHealth` 模型报告：`active_read`、`active_write`、`idle`、`waiters` 计数
**并且** 健康检查端点 `GET /health` 返回连接池状态 JSON
**并且** `tests/unit/test_connection_pool.py` 验证并发访问模式

### Story 1.7: Lance 存储基础

作为一名开发者，
我希望在 S3/MinIO 后端上使用 Arrow 原生 I/O 读写 Lance 数据集，
以便所有多模态数据以带零拷贝潜力的版本化列式格式存储。

**验收标准：**

**假设** 有一个可用的 MinIO 存储桶 `arrow-lake-test`（来自 docker-compose）
**当** 我创建一个 Lance 数据集，Schema 为 `pa.schema([pa.field("id", pa.string()), pa.field("modality", pa.string()), pa.field("created_at", pa.timestamp("us"))])`
**那么** 数据集创建在 `s3://arrow-lake-test/datasets/test.lance/`
**并且** `lance.dataset("s3://arrow-lake-test/datasets/test.lance/")` 可以将其读回为 Arrow Table
**当** 我向数据集追加 1000 行
**那么** 数据集版本递增到 2，两个版本均可读取
**并且** `dataset.version` 返回当前版本号
**并且** `tests/integration/test_lance_roundtrip.py` 验证与真实 MinIO 的读写一致性
**并且** Arrow 零拷贝在组件边界处验证（非读写地址比较）：Lance RecordBatch → DuckDB 查询产生共享内存引用；Lance RecordBatch → Daft DataFrame 共享底层 Arrow 数组缓冲区；Lance RecordBatch → PyTorch 张量使用 `pin_memory + non_blocking` 传输
**并且** `tests/integration/test_boundary_lance_duckdb.py` 和 `test_boundary_lance_daft.py` 通过缓冲区引用一致性验证零组件间拷贝

### Story 1.8: Catalog Actor（Ray Named Actor）

作为一名开发者，
我希望有一个集中式的 Catalog 作为 Ray Named Actor，注册数据集并通过 SQL 暴露元数据，
以便所有组件可以通过单一事实来源发现和查询可用数据集。

**验收标准：**

**假设** Ray 集群正在运行（本地，单节点）
**当** 我通过 `ray.remote(CatalogActor).remote()` 创建 CatalogActor
**那么** 该 Actor 注册为命名 Actor，可通过 `ray.get_actor("CatalogActor")` 检索
**当** 我调用 `catalog.register("my_table", uri="s3://arrow-lake-test/datasets/my.lance/", schema=arrow_schema, namespace="default")`
**那么** 数据集元数据存储在内嵌的 DuckDB 中，`namespace` 字段保留用于未来的多租户隔离
**并且** `catalog.list_datasets()` 返回包含 Schema 和行数信息的列表
**并且** `catalog.get_dataset("my_table")` 返回完整的 DatasetInfo，包含 Schema、列元数据、统计信息
**当** 我调用 `catalog.query_metadata("SELECT name, row_count FROM datasets WHERE modality = 'image'")`
**那么** DuckDB 返回匹配的数据集元数据
**并且** Actor 配置了 `max_restarts=3` 用于自动恢复
**并且** `tests/integration/test_catalog_actor.py` 验证 CRUD 操作和 SQL 查询

### Story 1.9: Docker Compose 本地开发

作为一名平台工程师，
我希望有一个 `docker compose up -d` 命令启动所有平台服务（MinIO、Ray、Jupyter），
以便任何开发者都能在几分钟内拥有一个功能完整的本地环境。

**验收标准：**

**假设** 项目根目录有 `docker-compose.yml`
**当** 我运行 `docker compose up -d`
**那么** 以下服务成功启动：MinIO（端口 9000）、Ray Head（端口 8265）、Ray Worker（可选 GPU）、Jupyter（端口 8888）
**并且** MinIO 在首次启动时创建默认存储桶 `arrow-lake`
**并且** Ray Dashboard 可在 `http://localhost:8265` 访问
**并且** Jupyter Notebook 可以 `import ray` 和 `import arrow_lake`
**并且** `docker-compose.yml` 配置了资源限制（CPU：4、内存：8GB）
**并且** 存在 `docker-compose.gpu.yml` overlay 用于 GPU 直通（当 `docker compose -f docker-compose.yml -f docker-compose.gpu.yml up -d`）
**并且** Prometheus `/metrics` 端点可在 `http://localhost:8000/metrics` 访问
**并且** 所有服务使用共享的 Docker bridge 网络，具有正确的服务间 DNS 解析

### Story 1.10: 平台启动冒烟测试

作为一名平台工程师，
我希望有一个端到端冒烟测试，验证所有服务按正确顺序启动且配置正确解析，
以便在构建功能之前确认整个平台引导正常工作。

**验收标准：**

**假设** `docker compose up -d` 已完成
**当** 我运行 `python -c "from arrow_lake import Lake; lake = Lake(); print(lake.health())"`
**那么** 健康检查返回 `{"status": "healthy", "services": {"minio": "ok", "ray": "ok", "catalog": "ok"}, "metrics": "accessible"}`
**当** 我运行 `pytest tests/smoke/test_platform_boot.py`
**那么** 所有 5 个冒烟测试通过：MinIO 连通性、Ray Actor 注册、Catalog CRUD、Lance 读写、/metrics 端点
**并且** 冒烟测试总执行时间在 30 秒以内
**并且** 测试日志包含服务启动顺序和健康检查时间戳
**并且** NFR-USE-01 门禁得到验证：从 git clone 到冒烟测试通过 < 30 分钟

---

## Epic 2: 数据版本管理

Maya 可以标记数据集版本、时间旅行到任意历史状态、并排比较版本、压缩存储、演进 Schema，并使用 pytest 验证数据正确性。

**FR：** FR-STOR-02、FR-STOR-03、FR-STOR-04、FR-STOR-05、FR-STOR-06、FR-PROC-07、FR-DEV-05

### Story 2.1: 每次写入自动版本管理

作为一名数据工程师，
我希望每次对 Lance 数据集的写入操作都自动创建新版本，
以便我永远不会丢失数据，并且无需手动备份即可恢复到之前的状态。

**验收标准：**

**假设** 一个 Lance 数据集在版本 2
**当** 我通过 `dataset.append(arrow_table)` 追加 500 新行
**那么** 数据集版本递增到 3
**并且** `dataset.version` 返回 3
**并且** `dataset.versions()` 返回 `[1, 2, 3]`
**并且** 版本 2 和版本 3 的数据均可独立查询
**并且** `tests/unit/test_versioning.py` 验证追加、合并和覆盖操作的版本自动递增

### Story 2.2: 重要版本的命名标签

作为一名数据工程师，
我希望用有意义的名称标记特定数据集版本（例如"production"、"experiment-v3"），
以便我可以快速引用重要里程碑而无需记住版本号。

**验收标准：**

**假设** 一个 Lance 数据集在版本 5
**当** 我调用 `dataset.create_tag("production", version=5)`
**那么** 标签"production"创建并指向版本 5
**并且** `dataset.list_tags()` 返回 `["production"]`
**当** 我调用 `dataset.checkout("production")`
**那么** 数据集读取器指向版本 5
**并且** `dataset.checkout("nonexistent")` 抛出 `TagNotFoundError`
**并且** 标签持久化在 Lance 元数据中，并在数据集重新加载后保留
**并且** `tests/unit/test_tags.py` 验证标签 CRUD 和签出操作

### Story 2.3: 时间旅行查询

作为一名数据工程师，
我希望读取数据集的任意历史版本而不修改当前版本，
以便我可以检查任意时间点的数据用于调试或审计。

**验收标准：**

**假设** 一个 Lance 数据集在版本 5
**当** 我调用 `lance.dataset(uri, version=2).to_table()`
**那么** 我收到一个 Arrow Table，包含版本 2 时的数据
**并且** 当前数据集保持在版本 5（无副作用）
**当** 我查询 `lance.dataset(uri, version=1).to_table()`
**那么** 我收到第一版本的数据
**并且** `tests/unit/test_time_travel.py` 验证按顺序读取多个历史版本

### Story 2.4: 版本差异

作为一名 ML 科学家，
我希望比较两个数据集版本以查看 Schema 变更、行增减和列修改，
以便我可以理解实验之间发生了什么变化。

**验收标准：**

**假设** 一个 Lance 数据集有版本 3 和版本 5
**当** 我调用 `dataset.diff(version_left=3, version_right=5)`
**那么** 结果包括：added_rows、removed_rows、schema_changes（新增/删除/修改的列）、column_stats_diff
**并且** schema_changes 列出具体的列名及其类型变更
**并且** `dataset.diff("production", "staging")` 支持标签名称
**并且** 差异输出可序列化为 JSON 用于日志记录
**并且** `tests/unit/test_version_diff.py` 验证在已知数据集变更上的差异准确性

### Story 2.5: 压缩

作为一名数据工程师，
我希望通过合并 Fragment 文件和回收已删除列的空间来压缩 Lance 数据集，
以便在数据集通过多次写入增长后查询性能保持快速。

**验收标准：**

**假设** 一个 Lance 数据集有 50+ 个小 Fragment 文件，来自多次追加操作
**当** 我调用 `dataset.compact()`
**那么** Fragment 文件数量显著减少（可衡量的缩减）
**并且** 所有现有数据保持可查询，结果一致
**并且** `dataset.version` 递增（压缩是一个写操作）
**并且** `dataset.optimize.compaction()` 配合可配置的 `target_fragment_size` 参数工作
**并且** `tests/integration/test_compaction.py` 验证：压缩前文件数、压缩后文件数、数据完整性、版本递增

### Story 2.6: Schema 迁移

作为一名开发者，
我希望在 Lance 数据集中添加、修改或删除列而无需全量数据重写，
以便 Schema 可以随项目成熟而演进，无需昂贵的迁移作业。

**验收标准：**

**假设** 一个 Lance 数据集有列 `[id, name, age]`
**当** 我通过 `dataset.alter_columns({"email": pa.string()})` 添加新列 `email`
**那么** 列被添加而不重写现有数据
**并且** 现有行的 `email = null`（NULL 安全）
**当** 我通过 `dataset.alter_columns({"age": pa.int64()})` 将 `age` 列类型从 `int32` 修改为 `int64`
**那么** 现有整数值被保留
**并且** 当我通过 `dataset.alter_columns({"age": None})` 删除列 `age`
**那么** 列被删除，存储空间在下一次压缩时回收
**并且** `tests/integration/test_schema_migration.py` 验证添加、修改类型和删除操作

### Story 2.7: 数据测试框架

作为一名数据工程师，
我希望有验证 Lance/Daft/DuckDB 结果的 pytest 断言用于数据正确性验证，
以便我可以构建能及早发现数据质量问题的回归测试。

**验收标准：**

**假设** 有 `arrow_lake.testing` 模块包含断言辅助函数
**当** 我在测试中写 `assert_table_has_schema(table, expected_schema)`
**那么** 如果 Arrow Schema 匹配则断言通过，失败时提供清晰的差异信息
**并且** `assert_row_count(table, expected=1000)` 验证行数
**并且** `assert_column_values_unique(table, "id")` 验证唯一性
**并且** `assert_column_within_range(table, "quality_score", min=0.0, max=1.0)` 验证数值范围
**并且** `assert_dataset_version(dataset, expected_version=5)` 验证 Lance 版本
**并且** `tests/unit/test_testing_framework.py` 验证所有断言辅助函数在通过和失败情况下的行为
**并且** 所有辅助函数在失败时产生清晰的错误消息，包含期望值与实际值

### Story 2.8: 数据集生命周期管理

作为一名数据工程师，
我希望删除和归档数据集，并正确清理 Lance 存储和目录元数据，
以便我可以管理存储成本并删除废弃或测试数据集，而不会产生孤立数据。

**验收标准：**

**假设** 目录中有一个已注册的数据集
**当** 我调用 `lake.catalog.delete_dataset("my_table", cascade=True)`
**那么** 所有 Lance 数据集版本和 Fragment 从 S3/MinIO 存储中移除
**并且** 数据集条目从 Catalog DuckDB 数据库中删除
**并且** 任何关联的死信表也被删除

**假设** 有一个已注册的数据集，我希望保留但从活跃使用中移除
**当** 我调用 `lake.catalog.archive_dataset("my_table")`
**那么** 数据集条目在 Catalog 中标记为 `status='archived'`
**并且** 数据集不再出现在 `catalog.list_datasets()` 中（除非 `include_archived=True`）
**并且** 底层 Lance 数据保持完整，可通过 `catalog.restore_dataset("my_table")` 恢复

**假设** 对一个被活跃流水线引用的数据集执行删除操作
**当** 尝试删除
**那么** 操作被拒绝，返回 `ErrorCode.DATASET_IN_USE` 和列出活跃引用的消息
**并且** `tests/integration/test_dataset_lifecycle.py` 验证删除、归档、恢复和使用中保护

---

## Epic 3: 多模态摄入

Maya 可以从本地 FS、S3 或 HTTP 摄入文本、图像和视频到统一的 Lance 表中，支持惰性 Blob 加载和自动缩略图生成。

**FR：** FR-ING-01、FR-ING-02、FR-ING-03、FR-ING-06、FR-ING-09、FR-PROC-01、FR-PROC-06

### Story 3.1: 本地和 S3 数据摄入

作为一名数据工程师，
我希望从本地文件系统和 S3/MinIO 摄入 text、CSV、JSON 和 Parquet 文件到统一格式，
以便我可以将本地和云存储中的结构化和半结构化数据整合到单个湖仓表中。

**验收标准：**

**假设** 有一个目录包含示例 CSV、JSON 和 Parquet 文件（本地文件系统）
**当** 摄入流水线被调用，源路径指向该目录
**那么** 所有支持的文件通过文件扩展名和 MIME 类型检查被检测并加载到 Daft DataFrame
**并且** 生成的 DataFrame 包含所有摄入文件的合并 Schema，列类型正确
**并且** 不支持的文件扩展名被记录日志并跳过，不抛出异常

**假设** 有一个 S3/MinIO 存储桶 URI（例如 `s3://my-bucket/data/`）包含 CSV 和 JSON 文件
**当** 摄入流水线以 S3 URI 作为源被调用
**那么** 文件通过 boto3/S3FS 列出和读取，使用 `daft.read_csv` / `daft.read_json` 抽象加载到 Daft
**并且** 认证凭证从环境变量解析（`AWS_ACCESS_KEY_ID`、`AWS_SECRET_ACCESS_KEY`、`AWS_ENDPOINT_URL`）

**假设** 有一个混合本地 + S3 协议的源配置
**当** 摄入流水线处理所有源
**那么** 每个源由相应的连接器处理，结果合并到单个 DataFrame
**并且** 每源摄入统计信息（行数、文件数、错误数）被报告
**并且** `tests/unit/test_ingest_local_s3.py` 使用模拟响应验证本地和 S3 连接器
**并且** 摄入吞吐量被测量：50,000 CSV 行（10 列，混合类型）基准在单个 CPU 核心上在 1 秒内完成（NFR-PERF-02 基线）

### Story 3.2: HTTP 源摄入与混合源合并

作为一名数据工程师，
我希望从 HTTP 端点摄入数据并将混合协议（本地 + S3 + HTTP）的结果合并到单个 DataFrame，
以便我可以从远程 API 和 URL 拉取数据，同时结合本地和云源。

**验收标准：**

**假设** 有一个指向 CSV 或 JSON 文件的 HTTP URL
**当** 摄入流水线以 HTTP URL 作为源被调用
**那么** 文件被流式传输并解析为 Daft DataFrame
**并且** HTTP 错误（4xx、5xx）被捕获并以描述性 `ErrorCode.HTTP_FETCH_FAILED` 抛出
**并且** HTTP 超时和重试可通过 `ARROW_LAKE__HTTP_TIMEOUT_SECONDS` 和 `ARROW_LAKE__HTTP_MAX_RETRIES` 配置

**假设** 有一个混合协议（本地 + S3 + HTTP）的源配置
**当** 摄入流水线处理所有源
**那么** 源之间的 Schema 合并冲突被解决：共享列使用其合并类型；源特定列以 NULL 填充
**并且** 每源摄入统计信息（行数、文件数、错误数）被报告
**并且** `tests/unit/test_ingest_http_mixed.py` 使用模拟响应验证 HTTP 连接器和混合协议合并

### Story 3.3: 图像摄入与缩略图生成

作为一名数据工程师，
我希望摄入 JPEG、PNG 和 WebP 图像，自动生成缩略图并提取 EXIF，
以便下游消费者可以浏览图像预览而无需加载全分辨率原图。

**验收标准：**

**假设** 有一个包含 JPEG、PNG 和 WebP 图像文件的目录
**当** 摄入流水线处理每张图像
**那么** 原始二进制数据存储为脱线 Blob 数据，同时生成 64x64 缩略图并存储
**并且** EXIF 元数据（相机制造商/型号、GPS 坐标、拍摄时间戳）被提取并存储在专用列中
**并且** 头部损坏的图像被拒绝，使用 `ErrorCode.IMAGE_DECODE_FAILED` 并写入死信表

**假设** 有一张超过 10,000 x 10,000 像素的图像
**当** 缩略图生成器处理该图像
**那么** 缩略图从缩小后的中间图像（最长边最大 4096px）生成，以避免过多内存使用
**并且** 缩略图尺寸保持精确的 64x64 或 256x256（根据配置）

**假设** 有一张没有 EXIF 数据的图像
**当** 尝试提取 EXIF
**那么** 所有 EXIF 列填充为 NULL，不抛出错误
**并且** 图像仍然通过摄入，不被拒绝

**假设** 配置为 `ARROW_LAKE__THUMBNAIL_SIZE=256`
**当** 摄入期间生成缩略图
**那么** 所有缩略图为 256x256 像素
**并且** `tests/unit/test_thumbnail.py` 验证尺寸、EXIF 提取和损坏图像处理

### Story 3.4: 视频关键帧提取

作为一名数据工程师，
我希望从视频文件的场景边界提取代表性关键帧，
以便视频内容可以与图像和文本一起被索引、搜索和预览。

**验收标准：**

**假设** 有一个包含 MP4 和 MKV 视频文件的目录
**当** 摄入流水线处理每个视频
**那么** 使用 PyAV 在检测到的场景边界提取关键帧
**并且** 每个关键帧存储为 JPEG Blob，带有 `timestamp_ms` 列记录其在视频中的位置
**并且** 每个视频至少提取一个关键帧（第一帧作为兜底）

**假设** 有一个 PyAV 场景检测失败或超时的视频（例如损坏或极短的视频）
**当** 尝试提取关键帧
**那么** 视频的第一帧被提取为唯一关键帧
**并且** 记录带有 `ErrorCode.SCENE_DETECTION_FALLBACK` 的警告，但摄入继续

**假设** 有一个 PyAV 无法打开的视频文件（不支持的编解码器、损坏的文件）
**当** 尝试提取关键帧
**那么** 视频被拒绝，使用 `ErrorCode.VIDEO_DECODE_FAILED`
**并且** 拒绝原因和视频元数据被写入死信表

**假设** 有一个 60 秒的视频，有 5 个场景变化
**当** 关键帧提取完成
**那么** 精确提取 5 个关键帧（如果包含第一帧则为 6 个），每个都有准确的 `timestamp_ms`
**并且** `tests/unit/test_video_ingest.py` 验证场景检测、兜底行为和损坏视频拒绝

### Story 3.5: 统一多模态表存储

作为一名数据工程师，
我希望将文本、图像、视频和音频存储在单个 Lance 表中，使用一致的 Schema，
以便我可以跨模态查询而无需连接多个表。

**验收标准：**

**假设** 有来自多个模态的摄入数据（文本 CSV、图像、视频）
**当** 数据写入 Lance
**那么** 所有行存储在单个 Lance 表中，Schema 为：`id (string), modality (string), source (string), created_at (timestamp), text_content (string), image_data (binary), video_data (binary)`
**并且** 模态特定列被填充，不相关的列包含 NULL（例如文本行的 `image_data`、`video_data` 为 NULL）
**并且** Schema 可扩展 — 未来模态（例如 audio_data）可通过 Schema 迁移（Story 2.6）添加而不破坏现有数据
**并且** NULL 安全操作返回正确结果（例如 `WHERE image_data IS NOT NULL` 仅返回图像行）

**假设** 有一批新行，包含现有 Lance 表 Schema 中不存在的列
**当** Schema 验证被应用（Lance 原生 Schema 强制；可插拔严格模式推迟到 Epic 4 Story 4.10，参见 FR-QUA-05）
**那么** 包含未知列的行被拒绝并记录警告日志；被拒绝的行在流水线错误指标中跟踪（死信表形式化推迟到 Epic 4 Story 4.8）
**并且** 有效行的摄入继续，不受中断

**假设** 有一个混合模态数据的统一 Lance 表
**当** 查询按 `modality = 'image'` 过滤
**那么** 仅返回图像行，包含正确的 `image_data` Blob
**并且** 谓词下推将过滤器委托给 Lance 的 scanner 以提高效率
**并且** `tests/integration/test_unified_table.py` 验证多模态写入、NULL 安全性和谓词下推

### Story 3.6: 多保真度 Blob 存储

作为一名数据工程师，
我希望以多个保真度级别（缩略图、预览、原始）存储媒体，支持惰性加载，
以便仅需要元数据或预览的查询避免昂贵的全分辨率 Blob I/O。

**验收标准：**

**假设** 有一张图像摄入到统一表中
**当** 摄入流水线存储该图像
**那么** 存储三个保真度级别：`thumbnail`（64x64）、`preview`（512x512）和 `original`（全分辨率）
**并且** 每个保真度级别可通过专用列或子列访问（例如 `image_data.thumbnail`、`image_data.preview`、`image_data.original`）

**假设** 有一个仅选择 `id` 和 `caption` 列的查询
**当** 查询被执行
**那么** 零 Blob I/O 发生 — 没有图像字节从磁盘读取
**并且** 查询延迟与纯文本表上的仅元数据扫描相当
**并且** 测试通过模拟 S3 客户端验证零 Blob I/O，该客户端跟踪 `get_object` 调用次数和传输总字节（期望：SELECT 中未包含的 Blob 列为 0 字节）

**假设** 有一个选择 `id` 并请求缩略图数据的查询
**当** 查询被执行
**那么** 仅加载缩略图 Blob，不加载预览或原图
**并且** 加载数据的字节大小受缩略图大小限制，而非原始图像大小

**假设** 有一个不再需要全保真度的原始媒体文件
**当** 配置了 Blob 生命周期策略（例如 `ARROW_LAKE__RETENTION_ORIGINAL_DAYS=90`）
**那么** 超过保留期的原始 Blob 有资格进行自动清理
**并且** 缩略图和预览保真度级别不受策略影响，始终保留
**并且** `tests/unit/test_multi_fidelity.py` 验证保真度级别存储和惰性加载行为

### Story 3.7: Daft DataFrame API 用于数据转换

作为一名数据工程师，
我希望有一个 Daft DataFrame 封装，提供 select、filter、sort、join 和 group 操作用于多模态数据，
以便我可以在写入结果之前使用熟悉的 DataFrame API 转换和查询摄入的数据。

**验收标准：**

**假设** 有一个从统一 Lance 表加载的 Daft DataFrame
**当** 调用 `.select("id", "modality", "caption")`
**那么** 返回一个仅包含指定列的新 DataFrame
**并且** 在调用 `.collect()` 之前不触发任何计算（惰性求值）

**假设** 有一个混合模态数据的 Daft DataFrame
**当** 调用 `.filter(daft.col("modality") == "image")` 后跟 `.sort("created_at", desc=True)`
**那么** 构建一个惰性计划，过滤为图像行并按创建时间戳降序排序
**并且** `.collect()` 执行计划并返回排序后的图像行

**假设** 有两个 Daft DataFrame — 一个包含图像嵌入，一个包含文本嵌入
**当** 调用 `.join(other_df, on="id", how="inner")`
**那么** 行按 `id` 匹配，结果中同时包含图像和文本嵌入列
**并且** join 操作在 `.collect()` 之前惰性执行

**假设** 有一个 `.collect()` 之后的 Daft DataFrame 结果
**当** 结果通过 `.to_arrow()` 转换
**那么** 返回一个有效的 PyArrow Table，包含正确的 Schema 和数据类型
**并且** 二进制列（image_data、video_data）保留为 `pa.binary()` 类型
**并且** `tests/unit/test_daft_api.py` 验证 select、filter、sort、join、groupby 和 to_arrow 转换

### Story 3.8: 媒体的惰性下载与解码

作为一名数据工程师，
我希望图像和视频保持存储直到像素访问被显式请求，
以便仅元数据的扫描和过滤操作避免下载和解码大型媒体文件的成本。

**验收标准：**

**假设** 有一个包含 1,000 图像行的统一 Lance 表，存储在 S3 中
**当** 执行查询 `SELECT id, caption FROM table WHERE modality = 'image'`
**那么** 零图像字节从 S3 下载，零解码操作发生
**并且** 查询完成，延迟与相同行数的纯文本表相当

**假设** 有一个请求像素数据的查询：`SELECT id, image_data.preview FROM table WHERE id = 'abc123'`
**当** 查询被执行
**那么** 仅匹配图像的预览保真度被下载和解码
**并且** 原始保真度保持存储，不被访问

**假设** 配置为 `ARROW_LAKE__DECODE_QUALITY=thumbnail`
**当** 任何媒体列被访问
**那么** 仅缩略图保真度被解码和返回（默认）
**并且** 更改配置为 `full` 导致后续访问时进行全分辨率解码

**假设** 有一个访问 50 行图像像素数据的查询
**当** 查询被执行
**那么** 媒体文件按需逐个惰性下载和解码（或按配置的批次），而非为整个表急切加载
**并且** 内存使用保持有界，与表总大小无关
**并且** `tests/integration/test_lazy_decode.py` 验证零下载元数据扫描和按需像素访问

### Story 3.9: 基础元数据搜索桥

作为一名数据工程师，
我希望使用简单 SQL 查询按文件名、模态和日期范围搜索已摄入的数据，
以便我可以在完整语义搜索可用之前（Epic 5）验证摄入数据的正确性并查找特定记录。

**验收标准：**

**假设** 有一个通过 Stories 3.1-3.3 摄入的混合模态数据 Lance 表
**当** 我通过 DuckDB 执行 `SELECT * FROM my_table WHERE filename LIKE '%report%'`
**那么** 返回匹配行，包含正确的 Blob 数据
**并且** 查询利用 Lance 谓词下推提高效率

**假设** 同一个表
**当** 我执行 `SELECT modality, COUNT(*) FROM my_table WHERE created_at >= '2026-01-01' GROUP BY modality`
**那么** 正确返回每种模态的行数
**并且** 查询在 100,000 行以内的表上 1 秒内完成

**假设** 有一个 SDK 调用 `lake.query("SELECT * FROM my_table WHERE modality = 'image' LIMIT 10")`
**当** 查询被执行
**那么** 结果作为可转换为 Arrow 格式的 Daft DataFrame 返回
**并且** 此桥接查询 API 与 Epic 5 中引入的完整搜索 API 保持一致
**并且** `tests/integration/test_metadata_search.py` 验证文件名过滤、日期范围和模态分组

---

## Epic 4: 嵌入与质量

Maya 可以在摄入时计算嵌入、应用可插拔的质量过滤器、去重内容，并将被拒绝的行持久化到死信表。

**FR：** FR-ING-04、FR-ING-05、FR-ING-07、FR-ING-08、FR-PROC-02、FR-PROC-05、FR-QUA-01、FR-QUA-02、FR-QUA-03、FR-QUA-04、FR-QUA-05

**风险 Spike：** NeMo Curator CPU 回退验证（高概率：NVIDIA 专属依赖）

### Story 4.1: 使用本地 HuggingFace 的文本嵌入

作为一名数据工程师，
我希望使用本地 HuggingFace 模型批量计算文本内容的向量嵌入，
以便文本数据可以通过向量相似度进行语义搜索和比较，而无需外部 API 依赖。

**验收标准：**

**假设** 有一个包含 10,000 文本行的 Lance 表（modality='text'）
**当** 嵌入流水线以 `model="BAAI/bge-small-en-v1.5"` 被调用
**那么** 嵌入被计算并存储在 `text_embedding` 列中，类型为 `pa.list_(pa.float32(), 384)`
**并且** 流水线在 GPU 可用时在 GPU 上运行，否则在 CPU 上

**假设** 有空或 NULL `text_content` 的行
**当** 嵌入流水线处理这些行
**那么** 它们收到 NULL 嵌入，不抛出错误

**假设** 有一个 10,000 文本行的批次
**当** 嵌入流水线以批次大小 128 运行
**那么** 精确处理 78 个完整批次和 1 个部分批次（16 行）
**并且** 嵌入列在完成时有 10,000 个非 NULL 值
**并且** `tests/unit/test_text_embedding_local.py` 验证批处理、NULL 处理和 GPU/CPU 执行

### Story 4.2: Ray Serve 嵌入后端与回退

作为一名数据工程师，
我希望将嵌入计算部署为 Ray Serve 端点，支持可扩展的分布式处理和自动回退，
以便嵌入流水线可以在负载下水平扩展，而无需外部 API 依赖。

**验收标准：**

**假设** 有一个部署了 Ray Serve 的 Ray 集群
**当** 嵌入流水线以 `ARROW_LAKE__EMBEDDING_BACKEND=ray_serve` 被调用
**那么** 嵌入通过 Ray Serve 部署计算，支持可扩展的分布式处理
**并且** 流水线在 Ray Serve 不可用时回退到本地 HuggingFace 推理
**并且** 回退转换记录带有 `ErrorCode.EMBEDDING_RAY_SERVE_FALLBACK` 的警告

**假设** 有一个在并发负载下的 Ray Serve 端点（多个流水线步骤请求嵌入）
**当** 流水线并行处理请求
**那么** Ray Serve 通过适当的排队和资源管理处理并发请求
**并且** `tests/unit/test_text_embedding_ray_serve.py` 验证 Ray Serve 调用、回退和并发行为

### Story 4.3: 外部 API 嵌入（OpenAI 兼容）

作为一名数据工程师，
我希望通过外部 API 端点（OpenAI 或兼容）计算嵌入，支持重试和错误处理，
以便我可以利用专有或云端托管的嵌入模型而无需自托管。

**验收标准：**

**假设** 配置为 `ARROW_LAKE__EMBEDDING_BACKEND=openai`
**当** 嵌入流水线被调用
**那么** 嵌入通过 OpenAI API（或 `ARROW_LAKE__EMBEDDING_API_BASE` 处的兼容端点）计算
**并且** API 错误（速率限制、超时、认证失败）以 `ErrorCode.EMBEDDING_API_ERROR` 捕获，并使用指数退避重试最多 3 次
**并且** API 密钥从环境变量 `ARROW_LAKE__EMBEDDING_API_KEY` 解析

**假设** 外部 API 不可达
**当** 嵌入流水线被调用
**那么** 流水线回退到本地 HuggingFace 推理，记录警告日志
**并且** `tests/unit/test_text_embedding_api.py` 使用模拟 API 响应验证 API 调用、重试逻辑和回退

**假设** 有一个包含 1,000 行 `text_content` 的 Lance 表
**当** 嵌入流水线以 `model="sentence-transformers/all-MiniLM-L6-v2"` 被调用
**那么** 嵌入在异步批次中计算（可通过 `ARROW_LAKE__EMBEDDING_BATCH_SIZE` 配置批次大小），存储在新列 `text_embedding` 中，类型为 `pa.list_(pa.float32(), dim)`，其中 dim 与模型输出维度匹配
**并且** 空或 NULL `text_content` 的行收到 NULL 嵌入，不抛出错误

**假设** 配置为 `ARROW_LAKE__EMBEDDING_BACKEND=ray_serve`
**当** 嵌入流水线被调用
**那么** 嵌入通过 Ray Serve 部署计算，支持可扩展的分布式处理
**并且** 流水线在 Ray Serve 不可用时回退到本地 HuggingFace 推理

**假设** 有一个 10,000 文本行的批次
**当** 嵌入流水线以批次大小 128 运行
**那么** 精确处理 78 个完整批次和 1 个部分批次（16 行）
**并且** 嵌入列在完成时有 10,000 个非 NULL 值
**并且** `tests/unit/test_text_embedding.py` 验证批处理、NULL 处理和后端回退

### Story 4.4: 图像嵌入计算

作为一名数据工程师，
我希望使用 GPU 加速计算 CLIP 和 SigLIP 图像嵌入，
以便图像可以通过跨模态向量相似度进行语义搜索和比较。

**验收标准：**

**假设** 有一个包含 500 图像行的 Lance 表（modality='image'）
**当** 嵌入流水线以 `model="openai/clip-vit-base-patch32"` 被调用
**那么** CLIP 嵌入被计算并存储在 `image_embedding` 列中，类型为 `pa.list_(pa.float32(), 512)`
**并且** GPU 可用时使用 GPU 加速（检测到 CUDA）

**假设** 配置为 `ARROW_LAKE__IMAGE_EMBEDDING_MODELS=clip-vit-base-patch32,siglip-so400m-patch14-384`
**当** 嵌入流水线被调用
**那么** CLIP 和 SigLIP 嵌入在单次传递中计算，存储在列 `image_embedding_clip` 和 `image_embedding_siglip` 中
**并且** 每列具有对应模型的正确维度

**假设** 图像摄入的缩略图为 256x256
**当** 计算 CLIP 嵌入
**那么** 默认使用缩略图保真度进行嵌入计算（可通过 `ARROW_LAKE__EMBEDDING_IMAGE_FIDELITY` 配置）
**并且** 切换到 `original` 保真度产生与模型期望输入分辨率一致的嵌入

**假设** 有一个 `image_data` 为 NULL 或损坏的图像行
**当** 嵌入流水线处理该行
**那么** 嵌入列设置为 NULL，记录带有 `ErrorCode.EMBEDDING_IMAGE_FAILED` 的警告
**并且** `tests/unit/test_image_embedding.py` 验证 GPU/CPU 回退和 NULL 处理

### Story 4.5: GPU/CPU 异构调度

作为一名平台运维人员，
我希望 Daft 在 GPU 可用时使用 GPU 加速，否则优雅地回退到 CPU，
以便系统在开发者笔记本（仅 CPU）和生产 GPU 集群上都能无需配置变更地工作。

**验收标准：**

**假设** 有一台具有 NVIDIA GPU 和 CUDA 可用的机器
**当** Daft 配置为 `use_gpu=True`
**那么** 图像嵌入和视频关键帧操作在 GPU 上执行
**并且** GPU 利用率在流水线执行期间通过 `nvidia-smi` 可见

**假设** 有一台没有 GPU 的机器（仅 CPU 环境）
**当** Daft 配置为 `use_gpu=True`（或默认值）
**那么** 所有操作回退到 CPU 执行，不抛出 CUDA 错误
**并且** 在流水线启动时记录一次带有 `ErrorCode.GPU_UNAVAILABLE_FALLBACK` 的警告
**并且** 流水线结果与 GPU 执行功能一致（嵌入值可能因浮点精度而不同）

**假设** 配置为 `ARROW_LAKE__GPU_MEMORY_FRACTION=0.8`
**当** GPU 操作被执行
**那么** Daft 最多分配 80% 的可用 GPU 内存
**并且** 内存不足错误通过减少批次大小并重试来处理，或回退到 CPU 处理剩余批次

**假设** 有一个包含 3 个 GPU 节点和 2 个仅 CPU 节点的异构集群
**当** 流水线部署在 Ray 上
**那么** GPU 加速任务调度到 GPU 节点，CPU 任务调度到仅 CPU 节点
**并且** `tests/unit/test_gpu_scheduling.py` 验证 GPU 检测、CPU 回退和 OOM 处理

### Story 4.6: 异步向量索引构建

作为一名数据工程师，
我希望向量索引（IVF_PQ 或 HNSW）在嵌入计算完成后异步构建，
以便摄入流水线不被索引构建阻塞，可以继续接受新数据。

**验收标准：**

**假设** 有一个包含 50,000 行已计算嵌入的 Lance 表
**当** 摄入流水线完成嵌入计算
**那么** 启动一个异步任务构建 IVF_PQ 索引（默认）或 HNSW 索引（可通过 `ARROW_LAKE__VECTOR_INDEX_TYPE` 配置）
**并且** 摄入流水线立即返回，不等待索引完成

**假设** 异步索引构建任务正在运行
**当** 查询流水线状态
**那么** 报告索引构建进度（状态：building/complete/failed、已索引行数、已用时间）
**并且** 表在索引构建期间仍可查询（通过暴力扫描）

**假设** 索引构建成功完成
**当** 对索引列执行向量相似度查询
**那么** 查询使用向量索引（通过查询计划检查验证），50,000 行表在 100ms 内返回结果
**并且** 索引元数据（类型、参数、构建时间戳、构建时的行数）记录在 Lance 数据集目录中

**假设** 索引构建因内存不足或损坏的嵌入而失败
**当** 检测到失败
**那么** 记录带有完整上下文的 `ErrorCode.INDEX_BUILD_FAILED` 错误
**并且** 表在没有索引的情况下完全可用（暴力扫描回退）
**并且** `tests/integration/test_index_build.py` 验证异步构建、进度报告和故障恢复

### Story 4.7: 基于内容寻址的去重

作为一名数据工程师，
我希望使用 SHA-256 精确哈希和感知哈希对近似重复图像进行去重，
以便数据集仅包含唯一内容，下游模型训练不受重复样本的偏差影响。

**验收标准：**

**假设** 有一个包含 1,000 行的 Lance 表，包括 50 个完全重复（相同二进制内容）
**当** 去重流水线以 `strategy=exact` 被调用
**那么** 在原始二进制内容上计算 SHA-256 哈希，存储在 `dedup_hash` 列中
**并且** 精确识别 50 个重复行，标记为 `is_duplicate=True`
**并且** 去重统计被报告：total_rows=1000、unique_rows=950、duplicates_found=50

**假设** 有一个包含图像行的 Lance 表，包括近似重复（不同压缩、轻微缩放、水印的同一图像）
**当** 去重流水线以 `strategy=perceptual` 被调用
**那么** 为所有图像行计算感知哈希（pHash），存储在 `dedup_phash` 列中
**并且** 汉明距离阈值内的近似重复被标记（可通过 `ARROW_LAKE__PERCEPTUAL_DUP_THRESHOLD` 配置）
**并且** 去重报告包含近似重复组的数量和大小

**假设** 精确和感知去重同时启用
**当** 去重流水线运行
**那么** 先应用精确去重，然后对剩余唯一行应用感知去重
**并且** `is_duplicate` 列反映两种策略的组合结果

**假设** 去重配置为 `ARROW_LAKE__DEDUP_ACTION=flag`（而非 `remove`）
**当** 流水线运行
**那么** 重复行被标记但不会从表中移除
**并且** `ARROW_LAKE__DEDUP_ACTION=remove` 导致标记行从活跃数据集中排除
**并且** `tests/unit/test_dedup.py` 验证精确哈希、感知哈希、组合策略和 flag/remove 操作

### Story 4.8: QualityFilter 注册

作为一名数据工程师，
我希望通过可插拔协议接口注册自定义质量过滤器，
以便我可以强制执行领域特定的数据质量规则，而无需修改核心流水线代码。

**验收标准：**

**假设** 有一个 `QualityFilter` 协议定义如下：
```python
class QualityFilter(Protocol):
    name: str
    def filter(self, row: dict) -> tuple[bool, str | None]: ...
```
**当** 实现了一个符合此协议的自定义过滤器 `LanguageFilter`
**那么** 过滤器可以通过 `registry.register("language_filter", LanguageFilter())` 注册
**并且** 过滤器出现在 `registry.list_filters()` 中，包含其名称和描述

**假设** 有三个已注册的过滤器：`text_length_filter`、`image_resolution_filter`、`language_filter`
**当** 质量流水线以 `filter_mode="all"`（AND 语义）运行
**那么** 一行只有在所有三个过滤器都返回 `(True, None)` 时才通过
**并且** 任何过滤器失败的行被拒绝，拒绝原因为第一个失败过滤器的返回

**假设** 有一个在 `filter(row)` 期间抛出意外异常的过滤器
**当** 异常被流水线捕获
**那么** 该行被拒绝，使用 `ErrorCode.FILTER_EXECUTION_ERROR`，异常回溯被记录
**并且** 流水线继续处理剩余行

**假设** 配置为 `ARROW_LAKE__QUALITY_FILTERS=text_length_filter,language_filter`
**当** 流水线启动
**那么** 仅加载指定过滤器并从注册表中应用
**并且** `tests/unit/test_quality_filter_registry.py` 验证注册、AND/OR 语义和异常处理

### Story 4.9: 内置质量过滤器

作为一名数据工程师，
我希望有内置的文本长度和图像分辨率质量过滤器，支持可配置阈值，
以便我可以开箱即用地强制执行通用质量标准，而无需编写自定义过滤器代码。

**验收标准：**

**假设** 有一个 `TextLengthFilter`，配置为 `min_chars=10, max_chars=10000`
**当** 处理一个 `text_content` 为 5 个字符的行
**那么** 过滤器返回 `(False, "text_length: 5 < min_chars(10)")`
**并且** 一个 5,000 字符的行返回 `(True, None)`

**假设** 有一个 `TextLengthFilter`，配置为 `min_chars=10`
**当** 处理一个 NULL `text_content` 的行
**那么** 过滤器返回 `(True, None)`（NULL 文本不受长度过滤器惩罚）
**并且** 过滤器仅评估 `text_content IS NOT NULL` 的行

**假设** 有一个 `ImageResolutionFilter`，配置为 `min_width=256, min_height=256`
**当** 处理一个尺寸为 128x128 的图像行
**那么** 过滤器返回 `(False, "image_resolution: 128x128 < min(256x256)")`
**并且** 一个 1024x768 的图像返回 `(True, None)`

**假设** `TextLengthFilter` 和 `ImageResolutionFilter` 都已注册并启用
**当** 质量流水线处理混合表
**那么** 文本行仅由 `TextLengthFilter` 评估（图像过滤器为空操作）
**并且** 图像行仅由 `ImageResolutionFilter` 评估（文本过滤器为空操作）
**并且** `tests/unit/test_builtin_filters.py` 验证阈值配置和模态特定评估

### Story 4.10: 死信持久化

作为一名数据工程师，
我希望被拒绝的行自动写入死信表，包含拒绝上下文，
以便我可以审计、诊断并可能恢复被拒绝的数据而不会丢失。

**验收标准：**

**假设** 有一个质量流水线，在多个过滤器中拒绝了 50 行
**当** 流水线完成
**那么** 所有 50 行被拒绝的行写入名为 `{original_table}_dead_letter` 的 Lance 表
**并且** 死信 Schema 包含：所有原始列 + `rejection_reason (string)` + `filter_name (string)` + `rejected_at (timestamp)`

**假设** 父表在死信写入时处于版本 5
**当** 死信表被创建或追加
**那么** 死信表被版本管理，`parent_version` 列设置为 5
**并且** 死信表维护其独立的版本历史

**假设** 有一个被 `TextLengthFilter` 拒绝的行，原因为"text_length: 3 < min_chars(10)"
**当** 行被写入死信表
**那么** `rejection_reason` 列包含精确的原因字符串
**并且** `filter_name` 列包含 `"TextLengthFilter"`
**并且** 所有原始行数据列完整保留

**假设** 有一个累积了 200 行被拒绝记录的死信表
**当** 数据工程师审查死信表
**那么** 他们可以按 `filter_name`、`rejected_at` 或 `rejection_reason` 查询以识别模式
**并且** `tests/integration/test_dead_letter.py` 验证 Schema、版本跟踪和可查询性

### Story 4.11: 质量统计报告

作为一名数据工程师，
我希望每次流水线运行后有一个全面的质量统计报告，
以便我可以评估数据健康状况、识别有问题的过滤器并跟踪质量趋势。

**验收标准：**

**假设** 有一个质量流水线，处理了 10,000 行，有 3 个活跃过滤器
**当** 流水线完成
**那么** 生成统计报告：total_rows=10000、passed_rows=9500、rejected_rows=500
**并且** 包含按过滤器的详细分布：filter_name、passed_count、rejected_count、pass_rate_percentage

**假设** 有质量流水线报告
**当** 调用 `report.to_json()`
**那么** 返回一个包含所有统计信息的 JSON 可序列化字典
**并且** JSON 与 Metaflow Cards 兼容，用于 Metaflow UI 中的可视化

**假设** 有一个流水线运行，`ImageResolutionFilter` 拒绝了 300 行，`TextLengthFilter` 拒绝了 200 行
**当** 检查按过滤器的详细分布
**那么** `ImageResolutionFilter` 显示 9700 通过 / 300 拒绝 / 97.0% 通过率
**并且** `TextLengthFilter` 显示 9800 通过 / 200 拒绝 / 98.0% 通过率

**假设** 有多次流水线运行
**当** 比较历史质量报告
**那么** 可以从序列化报告中导出趋势数据（每次运行的拒绝总数、按过滤器的拒绝趋势）
**并且** `tests/unit/test_quality_report.py` 验证报告结构、JSON 序列化和按过滤器准确性

### Story 4.12: Schema 验证关卡

作为一名数据工程师，
我希望有可配置的 Schema 验证，拒绝或调整列不匹配的行，
以便我可以根据用例强制执行严格的数据契约或优雅地处理演进中的 Schema。

**验收标准：**

**假设** 配置为 `ARROW_LAKE__SCHEMA_VALIDATION=strict`
**当** 摄入一行，包含目标 Lance 表 Schema 中不存在的列
**那么** 该行被拒绝，使用 `ErrorCode.SCHEMA_UNKNOWN_COLUMN`，拒绝原因中包含未知列名
**并且** 流水线在运行结束时记录拒绝行的汇总

**假设** 配置为 `ARROW_LAKE__SCHEMA_VALIDATION=strict`
**当** 一行有类型不匹配（例如 int64 列中有 string）
**那么** 该行被拒绝，使用 `ErrorCode.SCHEMA_TYPE_MISMATCH`，指定列名、期望类型和实际类型
**并且** 流水线不会尝试转换或强制类型

**假设** 配置为 `ARROW_LAKE__SCHEMA_VALIDATION=lenient`
**当** 一行有未知列
**那么** 未知列被删除并记录警告日志，该行以剩余有效列被摄入
**并且** 宽松模式下未知列不导致拒绝

**假设** 配置为 `ARROW_LAKE__SCHEMA_VALIDATION=lenient`
**当** 一行有兼容的类型不匹配（例如 int64 列中有 int32 值）
**那么** 值根据 PyArrow `can_cast` 的 safe 和 same_kind 规则自动转换为目标类型（int32→int64 ✓、float32→float64 ✓、int64→float64 ✓）
**并且** 不安全的转换（例如 float64→int64 可能丢失数据）触发警告并回退到严格模式拒绝
**并且** 不兼容的转换（例如 string→int64）即使在宽松模式下也导致拒绝
**并且** `tests/unit/test_schema_validation.py` 验证严格/宽松模式和类型强转行为

### Story 4.13: 质量分数作为 Lance 列

作为一名数据工程师，
我希望摄入时计算的质量分数存储为一级 Lance 列，支持下推谓词，
以便我可以在查询时按质量标准过滤和分析数据，而无需额外计算。

**验收标准：**

**假设** 有一个启用了质量评分的已完成摄入流水线
**当** 检查 Lance 表
**那么** 包含列：`quality_score (float32)`、`is_duplicate (bool)`、`nsfw_score (float32)`、`aesthetic_score (float32)`
**并且** `quality_score` 是一个综合分数（0.0-1.0），由各个子分数和过滤器结果派生
**并且** 高级子分数（`nsfw_score`、`aesthetic_score`）在 Epic 8（NeMo Curator GPU 流水线）中引入 — 在 MVP Core 中，如果未部署高级评分流水线，这些列为 NULL
**并且** `is_duplicate` 由去重流水线（Story 4.5）作为布尔标志填充

**假设** 有一个包含质量分数列的 Lance 表
**当** 执行查询 `SELECT * WHERE quality_score > 0.8`
**那么** 过滤器被下推到 Lance scanner（通过查询计划验证），仅物化匹配行
**并且** 查询性能与在任何原生 Lance 列上过滤相当

**假设** 有质量评分部分计算的行
**当** 查询表
**那么** 不适用的分数列包含 NULL（例如文本行的 `aesthetic_score` 为 NULL，或未部署高级评分流水线时）
**并且** `WHERE aesthetic_score IS NOT NULL` 正确返回仅有适用分数的行
**并且** `tests/integration/test_quality_predpushdown.py` 验证质量分数列上的谓词下推，包括 NULL 处理

---

## Epic 5: 语义搜索与分析

Raj 可以执行向量搜索、全文搜索、混合 RRF 搜索和 OLAP SQL 分析，支持流式结果和自适应索引选择。

**FR：** FR-QRY-01、FR-QRY-02、FR-QRY-03、FR-QRY-04、FR-QRY-05、FR-QRY-07、FR-CAT-03、FR-CAT-04

**MVP 核心路径端点** — Raj 的"顿悟时刻"

### Story 5.1: 向量搜索

作为一名数据分析师，
我希望在多模态嵌入上执行向量相似度搜索，
以便我可以跨文本和图像集合找到语义相似的内容。

**验收标准：**

**假设** 有一个包含 <1M 行嵌入向量的 Lance 数据集
**当** 执行向量搜索查询，包含查询向量、top_k=10 和 metric="cosine"
**那么** 结果作为 Arrow Table 返回，包含按相关性分数降序排列的前 10 条最相似记录
**并且** 搜索使用 HNSW 索引进行检索
**并且** 每条结果行包含记录的元数据列和距离分数

**假设** 有一个包含 >=1M 行嵌入向量的 Lance 数据集
**当** 执行向量搜索查询
**那么** 系统自动选择并使用 IVF_PQ 索引进行检索
**并且** 结果作为 Arrow Table 返回，包含相关性分数
**并且** 距离度量可配置为"cosine"或"l2"
**并且** top_k 是控制返回结果数量的可配置参数

**假设** 有一个所有结果相似度低于最小阈值的向量搜索查询
**当** 查询被执行
**那么** 返回空结果集，并带有清晰的指示（非错误）
**并且** 响应元数据包含实际找到的最大相似度分数用于诊断

**并且** `tests/integration/test_vector_search.py` 验证 HNSW 和 IVF_PQ 索引路径，包括空结果场景

### Story 5.2: 全文搜索

作为一名数据分析师，
我希望在文本和标题字段上执行全文搜索，
以便我可以按关键词、短语或 BM25 相关性排名查找记录。

**验收标准：**

**假设** 有一个通过 Lance Tantivy 后端在 text_content 和 caption 列上创建了 FTS 索引的 Lance 数据集
**当** 执行全文搜索查询，包含搜索字符串和 top_k=20
**那么** 结果作为 Arrow Table 返回，按 BM25 相关性分数降序排列
**并且** 搜索跨越 text_content 和 caption 列
**并且** top_k 是可配置参数
**并且** 结果包含匹配记录的元数据和 BM25 分数

**假设** 有一个没有 FTS 索引的数据集
**当** 执行全文搜索查询
**那么** 系统抛出清晰的错误，指示必须在搜索前创建 FTS 索引
**并且** `tests/integration/test_fts.py` 验证索引创建、搜索和缺少索引的错误处理

### Story 5.3: 带 RRF 的混合搜索

作为一名数据分析师，
我希望使用倒数排名融合（RRF）组合向量和全文搜索结果，
以便我可以利用语义和词法相关性获得更高质量的检索。

**验收标准：**

**假设** 有一个同时具有 HNSW/IVF_PQ 向量索引和 FTS 索引的 Lance 数据集
**当** 执行混合搜索，包含查询向量、文本查询和默认 alpha=0.7
**那么** 结果作为 Arrow Table 返回，包含 RRF 组合的相关性分数
**并且** 向量搜索结果贡献 70% 权重（alpha=0.7），文本搜索结果贡献 30% 权重（1-alpha=0.3）
**并且** 每种搜索方法的独立分数在融合前归一化
**并且** 结果按组合的 RRF 分数降序重新排列

**假设** 有一个带自定义 alpha 参数的混合搜索请求
**当** alpha 权重设置为 0.0 到 1.0 之间的值
**那么** 融合权重根据自定义 alpha 值应用
**并且** alpha=1.0 返回纯向量搜索结果
**并且** alpha=0.0 返回纯全文搜索结果
**并且** `tests/unit/test_hybrid_search.py` 验证 RRF 融合、alpha 权重和边界情况

### Story 5.4: OLAP 分析

作为一名数据分析师，
我希望通过 Daft SQL（主查询引擎）对 Lance 数据集执行 SQL 分析查询，DuckDB 作为目录级回退，
以便我可以在大规模多模态数据上执行聚合、分组和窗口函数。

**验收标准：**

**假设** 有一个在目录中注册的 Lance 数据集
**当** 通过 Daft SQL 执行带 GROUP BY 和聚合函数（SUM、AVG、COUNT）的 SQL 查询
**那么** 结果作为 Arrow Table 返回，包含正确的聚合值
**并且** Lance 谓词下推应用于在聚合之前在存储层过滤行

**假设** 有一个包含 COUNT(*) 的 SQL 查询
**当** 查询被执行
**那么** COUNT(*) 计算被下推到 Lance 存储层进行高效计数
**并且** 结果在不将所有行物化到内存中的情况下返回

**假设** 有一个包含窗口函数（ROW_NUMBER、RANK、LAG/LEAD）的 SQL 查询
**当** 查询被执行
**那么** 窗口函数结果正确计算，作为 Arrow Table 返回
**并且** 谓词下推应用于窗口函数之前的 WHERE 子句
**并且** `tests/integration/test_olap.py` 验证聚合、窗口函数和谓词下推

### Story 5.5: 流式结果

作为一名数据分析师，
我希望使用 Arrow RecordBatch 流式传输迭代大型查询结果，
以便我可以以恒定内存使用量处理任意大小的数据集。

**验收标准：**

**假设** 有一个包含 100M+ 行的 Lance 数据集
**当** 执行搜索或 SQL 查询，结果通过 `fetch_record_batch_reader()` 消费
**那么** 结果作为 Arrow RecordBatchReader 返回，逐批产生
**并且** 内存占用保持在 100MB 以下，与总结果集大小无关
**并且** 每个 RecordBatch 可以在下一个批次加载前被处理和释放

**假设** 有一个正在进行的流式查询
**当** 消费者从 RecordBatchReader 读取批次
**那么** 在任何给定时间仅保留一个批次（或有界数量的批次）在内存中
**并且** 消费者可以提前停止读取而不物化完整结果集
**并且** `tests/integration/test_streaming.py` 验证大型结果集的内存边界

### Story 5.6: 自适应索引选择

作为一名系统运维人员，
我希望系统根据数据集大小自动选择合适的向量索引类型，
以便查询性能得到优化，而无需手动索引管理。

**验收标准：**

**假设** 有一个行数少于可配置阈值（默认 1,000,000 行）的 Lance 数据集
**当** 执行向量搜索查询或请求索引
**那么** 系统选择并构建 HNSW 索引
**并且** 索引构建在定义的时间预算内完成

**假设** 有一个行数达到或超过可配置阈值（默认 1,000,000 行）的 Lance 数据集
**当** 执行向量搜索查询或请求索引
**那么** 系统选择并构建 IVF_PQ 索引
**并且** 索引创建在阈值被跨越时调度（例如追加操作后）

**假设** 运维人员指定了自定义阈值
**当** 阈值配置为非默认的 1M 值
**那么** 系统在所有后续索引选择决策中使用自定义阈值
**并且** 阈值作为可配置参数存储在目录或环境配置中
**并且** `tests/unit/test_adaptive_index.py` 验证 HNSW/IVF_PQ 选择逻辑和阈值跨越

### Story 5.7: 目录 SQL 查询与搜索路由

作为一名数据分析师，
我希望通过 SQL 查询目录元数据，并使用统一的搜索 API 路由到适当的搜索方法，
以便我可以在不知道使用哪个搜索后端的情况下搜索数据集。

**验收标准：**

**假设** 有通过 Catalog Actor 注册的目录元数据
**当** 执行针对目录元数据表的 SQL 查询
**那么** 查询通过 Catalog Actor 路由，返回准确的元数据结果
**并且** 目录查询支持按数据集名称、模态、Schema 和自定义元数据字段过滤

**假设** 调用统一搜索 API `lake.search(query, modality, top_k)`
**当** 查询参数是向量嵌入且指定了模态
**那么** API 路由到向量搜索（HNSW 或 IVF_PQ）并返回结果
**并且** 搜索类型根据查询参数的形状和内容自动检测

**假设** 调用统一搜索 API `lake.search(query, modality, top_k)`
**当** 查询参数是文本字符串
**那么** API 路由到全文搜索并返回 BM25 排名的结果
**并且** 当同时提供向量和文本查询参数时，API 路由到带 RRF 融合的混合搜索
**并且** `tests/integration/test_search_routing.py` 验证向量、文本和混合路由逻辑

### Story 5.8: 性能基准测试套件

作为一名系统运维人员，
我希望有一个全面的基准测试套件来验证性能非功能需求，
以便我可以跟踪性能回归并确保系统满足 SLA 目标。

**验收标准：**

**假设** 基准测试套件位于 `tests/benchmark/`
**当** 对具有 HNSW 索引的数据集执行向量搜索基准测试（NFR-PERF-01）
**那么** p95 向量搜索延迟在 10ms 以下
**并且** 基准测试结果作为结构化 JSON 记录，包含时间戳、数据集大小、索引类型、延迟百分位数和吞吐量

**假设** 基准测试套件
**当** 执行惰性求值基准测试（NFR-PERF-04）
**那么** 惰性求值在大数据集的过滤查询上比即时求值展示至少 100x 的性能提升
**并且** 结果作为结构化 JSON 记录用于回归跟踪

**假设** 基准测试套件
**当** 对 100M+ 行数据集执行流式内存基准测试（NFR-PERF-05）
**那么** 流式迭代期间内存占用保持在 100MB 以下
**并且** 结果作为结构化 JSON 记录，包含峰值内存测量

**假设** 有 `tests/benchmark/` 中的所有基准测试脚本
**当** 执行完整基准测试套件
**那么** 每个基准测试产生一个结构化 JSON 结果文件
**并且** 结果可以跨运行比较以检测性能回归

### Story 5.9: 数据导出为标准格式

作为一名数据工程师，
我希望将 Lance 表数据和查询结果导出为标准格式（Parquet、CSV），
以便下游工具和团队可以在不使用 Lance 或 Arrow 原生读取器的情况下消费 Arrow Lake 数据。

**验收标准：**

**假设** 有一个混合模态数据的 Lance 表
**当** 我调用 `lake.export("my_table", format="parquet", path="s3://output/my_table.parquet")`
**那么** 数据导出为 Parquet 文件，保留 Schema、数据类型和 null 处理
**并且** 嵌入向量列作为 Parquet LIST<DOUBLE> 列保留

**假设** 有一个来自混合搜索的查询结果
**当** 我调用 `result.export("csv", path="output/search_results.csv")`
**那么** 搜索结果导出为 CSV，包含分数列
**并且** Blob 列（image_data、video_data）从 CSV 导出中排除，记录警告日志

**假设** 有一个 1M 行表的导出操作
**当** 导出运行
**那么** 使用 Daft 的流式写入避免将整个表加载到内存
**并且** `tests/integration/test_data_export.py` 验证 Parquet、CSV 导出和流式行为

---

## Epic 6: 流水线编排与集成

Maya 可以使用 Metaflow 定义自动化数据流水线，具备三级自愈能力（重试/分类/回滚）、定时执行和基于标签的运行跟踪。

**FR：** FR-ORCH-01、FR-ORCH-02、FR-ORCH-03、FR-ORCH-05a、FR-ORCH-05b、FR-ORCH-05c、FR-ORCH-06、FR-ORCH-07、FR-PROC-08、FR-PROC-09

**集成 Story：** Maya E2E 流水线 — 1000 条混合质量记录，4 个步骤（ingest -> quality -> embed -> search），< 45 分钟，TTV + /metrics 可观测。

### Story 6.1: Metaflow FlowSpec 定义

作为一名流水线开发者，
我希望使用 Metaflow FlowSpec 类和标准装饰器定义批处理数据流水线，
以便我可以创建结构化的、可复现的数据处理工作流。

**验收标准：**

**假设** 有一个 Python 文件定义了 Metaflow FlowSpec 子类
**当** 类以 `@project` 装饰器装饰用于流水线配置
**那么** 流水线注册到 Arrow Lake 项目命名空间
**并且** 流水线配置（名称、描述、标签）存储在项目元数据中

**假设** 有一个 FlowSpec 类，方法以 `@step`、`@batch` 和 `@card` 装饰
**当** 本地执行 `python flow.py run`
**那么** 每个装饰步骤按定义的线性或分支拓扑执行
**并且** `@batch` 步骤可以指定资源需求（CPU、内存、GPU）
**并且** `@card` 步骤产生运行完成后可访问的可视化制品

**假设** 有一个有效的 FlowSpec 流水线
**当** 通过 `python flow.py run` 本地执行
**那么** 所有步骤成功完成，输入/输出制品在步骤之间传递
**并且** 运行状态和制品存储在 Metaflow 的本地元数据存储中
**并且** `tests/unit/test_flowspec.py` 验证步骤拓扑、制品传递和本地执行

### Story 6.2: 通过 Ray 集群执行

作为一名流水线开发者，
我希望在 Ray 集群上执行 Metaflow 流水线进行分布式处理，
以便我可以将流水线步骤扩展到单台机器资源之外。

**验收标准：**

**假设** 有一个有效的 FlowSpec 流水线，带有 `@batch` 装饰的步骤
**当** 执行 `python flow.py run --with ray`
**那么** 流水线提交到配置的 Ray 集群
**并且** `@batch` 步骤根据资源规范分配到 Ray Worker

**假设** 有一个集成了 Ray Data 的流水线
**当** 流水线处理分布式数据集
**那么** Ray Data 处理集群节点间的数据分区和分布
**并且** 每个 Worker 独立处理其分配的数据分区

**假设** `@batch` 步骤上有资源规范（例如 CPU=4、GPU=1、memory=16GB）
**当** 流水线在 Ray 上运行
**那么** 每个步骤在集群上分配指定的资源
**并且** 如果集群容量不足，步骤等待资源可用
**并且** `tests/integration/test_ray_execution.py` 验证分布式步骤执行和资源分配

### Story 6.3: 带指数退避的瞬态重试

作为一名流水线开发者，
我希望流水线步骤在瞬态故障时自动重试，使用指数退避，
以便瞬时的基础设施问题不会导致流水线失败。

**验收标准：**

**假设** 有一个以 `@retry(max_attempts=3, min_backoff=1, max_backoff=60)` 装饰的流水线步骤
**当** 步骤因 Spot Worker 抢占或网络错误而失败
**那么** 步骤自动重试，使用从 `min_backoff` 秒开始的指数退避
**并且** 每次重试之间退避时间翻倍（1s、2s、4s、...）
**并且** 退避时间上限为 `max_backoff` 秒
**并且** 步骤最多重试 `max_attempts` 次（含初始尝试）

**假设** 有一个在所有重试尝试中都失败的步骤
**当** 最后一次重试尝试失败
**那么** 步骤失败传播到错误分类处理器
**并且** 重试历史（尝试次数、退避时长、错误消息）被记录用于调试

**假设** 有一个在重试尝试中成功的步骤
**当** 步骤在一次或多次重试后成功完成
**那么** 流水线正常继续到下一步骤
**并且** 重试历史记录在运行元数据中
**并且** `tests/unit/test_retry.py` 验证退避时间、最大尝试次数和重试成功行为

### Story 6.4: 错误分类处理器

作为一名流水线运维人员，
我希望流水线错误被自动分类到不同类别，
以便我可以区分可重试的瞬态错误和需要干预的致命错误。

**验收标准：**

**假设** 有一个以 `@catch` 处理器装饰的流水线步骤
**当** 步骤执行期间发生错误
**那么** 错误被分类到以下四个类别之一：TRANSIENT、RESOURCE、VALIDATION 或 FATAL
**并且** TRANSIENT 错误（网络超时、Spot 抢占、临时 S3 503）被标记为使用指数退避重试
**并且** RESOURCE 错误（内存不足、磁盘满、Ray Actor 崩溃）被标记为使用资源调整重试（增加内存/副本）
**并且** VALIDATION 错误（按 Story 4.10 规则的 Schema 不匹配、缺少必需字段、类型转换失败）被标记为不可重试
**并且** FATAL 错误（数据损坏、认证失败、Lance 版本不可恢复状态）被标记为不可重试并触发回滚（Story 6.5）
**并且** 模糊错误（不可分类）默认为 FATAL，记录警告日志供人工审查

**假设** 有一个已分类的错误事件
**当** 错误被记录
**那么** 日志包含结构化错误上下文：错误类别、原始异常类型、消息、堆栈跟踪、步骤名称和 run_id
**并且** 错误上下文可通过目录 SQL 查询用于事后分析
**并且** `tests/unit/test_error_classifier.py` 验证所有四个错误类别和日志输出

### Story 6.5: 回滚到上次已知良好状态

作为一名流水线运维人员，
我希望在致命错误时数据集自动回滚到上次已知良好版本，
以便下游消费者不会暴露于部分写入或损坏的数据。

**验收标准：**

**假设** 有一个写入 Lance 数据集并遇到 FATAL 错误的流水线
**当** 错误分类处理器确定错误为 FATAL
**那么** Lance 数据集签出到上次已知良好版本
**并且** 上次已知良好版本标识符在 Metaflow `@catch` 处理器中作为检查点存储于步骤执行之前

**假设** 有一个数据集回滚操作
**当** 回滚完成
**那么** 数据集恢复到失败流水线步骤之前的状态
**并且** 包含被拒绝记录的死信表被保留
**并且** 流水线执行日志被保留用于调试

**假设** 有一个部分成功后失败的流水线
**当** 触发回滚
**那么** 仅由失败步骤（及后续步骤）修改的数据集被回滚
**并且** 由之前成功步骤修改的数据集保持在其当前版本
**并且** MVP 范围仅支持线性流水线回滚；分支流水线（扇出/扇入）回滚推迟到未来增强，需要显式的按分支检查点语义
**并且** `tests/integration/test_rollback.py` 验证检查点恢复和部分回滚语义

### Story 6.6: 定时流水线执行

作为一名流水线运维人员，
我希望按定义的间隔调度流水线进行周期性执行，
以便数据处理自动运行而无需手动触发。

**验收标准：**

**假设** 有一个以 `@schedule(daily="08:00")` 装饰的 FlowSpec 流水线
**当** Metaflow 调度器活跃
**那么** 流水线在配置时区的每天 08:00 执行
**并且** 执行历史带时间戳和状态被跟踪

**假设** 有一个以 `@schedule(hourly=True)` 装饰的 FlowSpec 流水线
**当** Metaflow 调度器活跃
**那么** 流水线在每小时整点执行

**假设** 有一个以 `@schedule(cron="0 2 * * 1-5")` 装饰的 FlowSpec 流水线
**当** Metaflow 调度器活跃
**那么** 流水线根据 cron 表达式在周一至周五的 02:00 执行

**假设** 有在 Metaflow Config YAML 中定义的调度配置
**当** 调度器启动
**那么** 调度定义从 YAML 配置加载
**并且** 调度状态（active、last_run、next_run、failures）被跟踪，可通过目录查询
**并且** `tests/unit/test_scheduler.py` 验证每日、每小时和 cron 调度配置

### Story 6.7: 基于标签的运行跟踪和恢复

作为一名流水线运维人员，
我希望使用自动生成的标签跟踪流水线运行，并从检查点恢复失败的运行，
以便我可以管理长时间运行的流水线而无需从头重新开始。

**验收标准：**

**假设** 有一个 Metaflow 流水线执行
**当** 流水线启动
**那么** 从 Metaflow run_id 自动生成唯一的运行标签
**并且** 标签与运行期间产生的所有制品、日志和元数据关联

**假设** 有一个在特定步骤失败的流水线运行
**当** 执行 `flow.py resume --run-id RUN_ID`
**那么** 流水线从最后成功完成步骤的检查点恢复
**并且** 原始运行的中间制品被复用
**并且** 检查点之后的步骤被重新执行

**假设** 有多个带标签的流水线运行
**当** 通过目录 SQL 执行运行历史查询
**那么** 所有运行列出，包含其标签、状态、开始时间、结束时间和步骤级状态
**并且** 结果可以按标签、状态或时间范围过滤
**并且** `tests/unit/test_run_tracking.py` 验证标签生成、恢复和历史查询

### Story 6.8: 通过 Ray 进行分布式处理

作为一名流水线开发者，
我希望使用 Ray 的 foreach API 在 Ray 集群上进行并行处理，
以便我可以水平扩展数据处理工作负载，具备容错能力。

**验收标准：**

**假设** 有一个在 Ray 集群上分区分布的数据集
**当** 流水线步骤使用 Ray foreach API 应用处理函数
**那么** 函数在所有可用 Ray Worker 上并行执行
**并且** 每个 Worker 独立处理其分配的数据分区
**并且** 结果被收集并合并到统一输出数据集

**假设** AutoScale 配置指定 min_workers=2 和 max_workers=10
**当** 处理工作负载增加
**那么** Ray 自动扩展 Worker 到 max_workers 限制
**并且** 当工作负载减少，Ray 缩减到 min_workers
**并且** GPU Worker 在需要 GPU 处理时包含在 AutoScale 配置中

**假设** 分布式处理期间 Ray Worker 故障
**当** 一个 Worker 崩溃或变得无响应
**那么** 失败 Worker 的任务自动重新调度到其他可用 Worker
**并且** 处理继续，无需人工干预
**并且** `tests/integration/test_ray_foreach.py` 验证并行执行、自动扩展和容错

### Story 6.9: 远程数据加载器模式

作为一名流水线开发者，
我希望 CPU Worker 预处理数据并以零拷贝方式传输到 GPU Worker 进行训练，
以便通过消除 CPU 预处理瓶颈最大化 GPU 利用率。

**验收标准：**

**假设** 有一个同时具有 CPU 和 GPU Worker 池的流水线
**当** 数据被加载处理
**那么** CPU Worker 对数据批次执行解码和转换
**并且** 预处理批次以零拷贝传输语义放置在 Ray Object Store 中
**并且** GPU Worker 直接从 Ray Object Store 读取预处理批次进行训练

**假设** 有一个 CPU 预处理速度不定的训练工作负载
**当** GPU Worker 消费数据的速度快于 CPU Worker 预处理速度
**那么** 预取队列深度确保 GPU Worker 不会饥饿
**并且** 预取队列深度是可配置参数（默认：提前 2 个批次）

**假设** 有远程数据加载器流水线
**当** 测量处理吞吐量
**那么** 持续训练期间 GPU 利用率保持在 80% 以上
**并且** CPU 预处理不会成为 GPU 吞吐量的瓶颈
**并且** `tests/integration/test_remote_dataloader.py` 验证零拷贝传输和预取行为
**并且** PyTorch DataLoader 集成验证 `pin_memory=True` 和 `non_blocking=True` 传输：一个从 Ray Object Store 消费 Arrow RecordBatches 的 DataLoader 向 GPU 张量提供数据，无需 CPU 序列化瓶颈（NFR-PERF-06）

### Story 6.10: Maya E2E 流水线集成

作为 Maya（产品负责人），
我希望运行一个完整的端到端流水线，处理 1000 条真实混合质量记录，经过摄入、质量过滤、嵌入和搜索，
以便我可以验证整个平台作为集成系统在目标时间预算内正常工作。

**验收标准：**

**假设** 有 1000 条真实记录，混合质量（噪声文本、低分辨率图像、缺失字段）
**当** 执行 4 步流水线：ingest -> quality filter -> embed -> search
**那么** 所有记录被摄入到 Lance 数据集
**并且** 低质量记录被路由到死信表，包含拒绝原因
**并且** 剩余记录被嵌入，存储其向量表示
**并且** 对嵌入记录的搜索查询返回相关结果

**假设** 4 步流水线正在执行 1000 条混合质量记录
**当** 流水线执行完成
**那么** 总执行时间在 45 分钟以内
**并且** 执行可通过 TTV（time-to-value）验证测量

**假设** 流水线正在运行
**当** 执行期间查询 `/metrics` 端点
**那么** 返回可观测指标，包括：已处理记录数、步骤持续时间、错误计数、死信计数和吞吐量
**并且** 指标随流水线进展实时更新

**假设** 有被质量过滤器拒绝的记录
**当** 流水线完成
**那么** 死信表填充被拒绝记录及其拒绝原因
**并且** 死信表可通过目录 SQL API 查询

### Story 6.11: Catalog 读副本实现高可用

作为一名平台运维人员，
我希望有一个 Catalog 读副本，当主 Ray Named Actor 不可用时可以从 DuckDB 数据文件启动，
以便在 Ray GCS 故障或 Catalog Actor 重启期间查询和元数据操作继续正常工作。

**验收标准：**

**假设** 主 CatalogActor 正常运行
**当** 通过 `lake.catalog.query_metadata(...)` 执行只读查询
**那么** 查询由主 CatalogActor 正常服务

**假设** 主 CatalogActor 不可用（崩溃或 Ray GCS 故障）
**当** 执行只读查询
**那么** 自动从 DuckDB 数据文件启动只读 Catalog 副本
**并且** 副本服务 `list_datasets()`、`get_dataset()` 和 `query_metadata()` 操作
**并且** 写操作（`register`、Schema 变更）返回 `ErrorCode.CATALOG_WRITE_UNAVAILABLE` 并附带清晰消息

**假设** 主 CatalogActor 恢复（通过 Ray max_restarts）
**当** 执行下一次读查询
**那么** 主 Actor 恢复服务所有操作
**并且** `tests/integration/test_catalog_read_replica.py` 验证故障转移和恢复行为

### Story 6.12: 轻量级生产部署包

作为一名平台工程师（Sam），
我希望有一个简化的部署包，将 docker-compose 与生产就绪的默认值和健康检查封装在一起，
以便我可以在不使用完整 K8s/Helm 复杂性的情况下验证到生产环境的部署路径。

**验收标准：**

**假设** Arrow Lake 项目已实现所有核心 Epic（1-5）
**当** 我运行 `docker compose -f docker-compose.prod.yml up -d`
**那么** 平台以生产调优的默认值启动：结构化日志输出到 stdout、指标启用、`/health` 健康检查端点
**并且** 健康检查返回 `{"status": "ok", "catalog": "available", "storage": "accessible"}`，HTTP 200

**假设** 生产 docker-compose 正在运行
**当** 我运行 `curl http://localhost:8000/health`
**那么** 响应指示启动后 5 秒内所有组件健康
**并且** `docker compose logs` 显示带关联 ID 的结构化 JSON 日志

**假设** 有一个包含生产 S3 端点和凭证的 `.env.production` 文件
**当** 生产 compose 启动
**那么** 配置从 `.env.production` 加载，4 层覆盖（代码默认值 → .env → 环境变量 → Metaflow YAML）
**并且** `tests/integration/test_prod_compose.py` 针对生产 compose 文件验证健康检查、指标端点和生产日志

---

## Epic 7: 生产与可观测性

Sam 可以通过 Helm 部署到 K8s、利用弹性 GPU 突发扩展、通过 Prometheus/Grafana 仪表板监控，并通过 CLI 管理平台。

**FR：** FR-DEV-02、FR-DEV-07、FR-ORCH-04、FR-ORCH-08、FR-PROC-03、FR-STOR-07、FR-OBS-01、FR-OBS-02、FR-OBS-03、FR-OBS-04、FR-OBS-05、FR-OBS-06

**MVP：** 生产（第 3-4 个月：部署+可观测性，第 4-6 个月：扩展+安全）

### Story 7.1: Jupyter Notebook 集成

作为一名数据科学家，
我希望有一个预配置的 Jupyter 环境，预装 arrow_lake、ray 和 daft 导入，
以便我可以立即开始探索和查询数据集，无需手动环境搭建。

**验收标准：**

**假设** 有一个运行中的 Docker Compose 环境，Jupyter 服务通过 `docker compose up -d` 启动
**当** 在浏览器中打开 `http://localhost:8888`
**那么** Jupyter Lab 启动，Python 内核预装了 `arrow_lake`、`ray`、`daft`、`lancedb`、`duckdb` 和 `pyarrow`，可直接导入
**并且** 内核在 `!pip install` 命令后自动重启以加载新安装的包
**并且** `docs/examples/` 目录至少包含 `quickstart.ipynb` 和 `hybrid_search.ipynb` 作为可运行的 Notebook
**并且** `quickstart.ipynb` 演示创建数据集、摄入示例数据和执行基本向量搜索
**并且** `hybrid_search.ipynb` 演示混合向量 + 全文搜索，支持可配置 alpha 权重
**并且** 所有示例 Notebook 在本地环境上端到端执行无错误
**并且** `tests/integration/test_jupyter.py` 通过 nbconvert 验证内核启动、Lance 连通性和示例 Notebook 执行

### Story 7.2: CLI 用于常用操作

作为一名开发者或数据工程师，
我希望有一个用于常用 Arrow Lake 操作的命令行界面，包括 ingest、search、status 和 version，
以便我可以快速与平台交互，而无需为日常任务编写 Python 脚本。

**验收标准：**

**假设** `arrow_lake` 包在当前环境中已安装
**当** 我运行 `arrow-lake --help`
**那么** CLI 显示带颜色的输出，包含子命令：`ingest`、`search`、`status`、`version`
**并且** `arrow-lake version` 打印已安装版本、Python 版本和核心依赖版本（Daft、Ray、Metaflow、Lance），以格式化表格显示
**并且** `arrow-lake ingest --source s3://my-bucket/data --table my_data --modality text` 将文件从源摄入到名为 `my_data` 的 Lance 表
**并且** `arrow-lake search --query "autonomous driving" --modality image --top-k 10` 返回前 10 条图像结果，以格式化表格显示分数
**并且** `arrow-lake search --query "machine learning" --modality text --top-k 5 --alpha 0.7` 使用指定 alpha 权重执行混合搜索
**并且** `arrow-lake status` 列出所有已注册数据集，包含行数、列 Schema 和最后更新时间戳
**并且** 错误消息以清晰的彩色格式显示（红色为错误、黄色为警告、绿色为成功）
**并且** `tests/unit/test_cli.py` 使用 click.testing.CliRunner 和临时测试数据集验证所有子命令

### Story 7.3: Argo Workflows 基础部署

作为一名 DevOps 工程师，
我希望通过 `python flow.py --with ray argo-workflows create` 将 Metaflow 流水线部署为 Kubernetes 上的 Argo Workflows，
以便批处理流水线在生产环境中可靠运行，使用原生 K8s 编排和制品管理。

**验收标准：**

**假设** 有一个在 `flows/` 中定义的 Metaflow FlowSpec，配置了 Ray 集成
**当** 我运行 `python flow.py --with ray argo-workflows create`
**那么** Argo 生成一个 Workflow YAML 清单，包含每个步骤的 RayJob 模板
**并且** Workflow YAML 包含 Ray Head 服务和可配置的 Worker 副本
**并且** 步骤间的制品传递使用 Argo 制品卷（S3 支持）用于模型和数据输出
**并且** 生成的 YAML 通过 `kubectl apply --dry-run=client` 验证
**并且** Workflow 包含与 Ray Worker 配置匹配的资源请求和限制
**并且** `tests/integration/test_argo_deploy.py` 验证 YAML 生成和针对测试 FlowSpec 的 dry-run

### Story 7.4: CronWorkflow 调度与高级 Argo 功能

作为一名 DevOps 工程师，
我希望将流水线调度为 CronWorkflow 并管理制品卷的生命周期策略，
以便批处理流水线按自动调度运行，具有适当的资源生命周期管理。

**验收标准：**

**假设** 有一个以 `@schedule(cron="0 2 * * *")` 装饰的 Metaflow FlowSpec
**当** 我运行 `python flow.py --with ray argo-workflows create --with cron`
**那么** 生成一个 CronWorkflow YAML，每天凌晨 2 点调度流水线
**并且** CronWorkflow 继承与基础 Workflow 相同的 RayJob 模板和制品卷

**假设** 有一个配置了制品卷的 CronWorkflow
**当** 工作流运行多天
**那么** 制品卷具有可配置的保留策略（例如 `ARROW_LAKE__ARGO_ARTIFACT_RETENTION_DAYS=30`）
**并且** 过期制品自动清理，防止无界存储增长

**假设** 有一个需要访问外部密钥的步骤（例如 S3 凭证、API 密钥）
**当** Workflow YAML 被生成
**那么** K8s Secret 引用从 Metaflow Config YAML 注入到 RayJob 模板
**并且** `tests/unit/test_argo_cron.py` 验证 CronWorkflow 生成、制品保留和密钥注入

### Story 7.5: 弹性 GPU 突发扩展

作为一名平台运维人员，
我希望系统根据任务队列深度自动将 GPU Worker Pod 从 0 扩展到 8，空闲时缩回 0，
以便在空闲期间最小化 GPU 计算成本，同时在 SLA 内处理突发工作负载。

**验收标准：**

**假设** 有一个部署在 Kubernetes 上的 Ray 集群，配置了 GPU 节点池并启用了自动扩展
**当** 向任务队列提交 100 个嵌入任务的批次
**那么** Ray 自动扩展器增量配置 GPU Worker Pod，直到队列深度解决（最多 8 个 Worker）
**并且** 从 0 到 8 个 GPU Worker 的扩展在 5 分钟内完成（NFR-SCALE-05）
**并且** Spot GPU 实例在可用时被优先使用，Spot 利用率超过总 GPU 时间的 70%（NFR-COST-03）
**并且** 当 Spot 容量不可用时使用按需 GPU 实例作为回退
**并且** 当任务队列在配置的空闲超时内为空时，Worker 缩减到 0
**并且** 支持分数 GPU 扩展：Worker 可以请求 0.5 GPU 增量（NFR-SCALE-04）
**并且** 扩展事件作为结构化 JSON 记录，包含 `event_type`、`target_replicas`、`current_replicas` 和 `timestamp`
**并且** `tests/integration/test_elastic_burst.py` 使用模拟 Ray 自动扩展器验证扩展/缩减时间

### Story 7.6: SQL 查询支持

作为一名数据分析师，
我希望通过 Daft SQL 和 DuckDB 两种接口使用标准 SQL 查询 Arrow Lake 数据集，
以便我可以在不学习领域特定 API 的情况下执行即席分析。

**验收标准：**

**假设** 有一个已注册的 Lance 数据集，包含文本、图像元数据、嵌入向量列和质量分数
**当** 我通过 Daft SQL 接口执行 `df.sql("SELECT * FROM my_table WHERE modality = 'image' AND quality_score > 0.8")`
**那么** 查询返回匹配行，作为可转换为 Arrow 格式的 Daft DataFrame
**并且** 谓词下推被应用，使 Lance 仅扫描相关 Fragment
**并且** 我可以通过 DuckDB 直接访问（仅用于目录和轻量级查询）执行复杂 OLAP 查询：`SELECT modality, COUNT(*) as cnt, AVG(quality_score) FROM my_table GROUP BY modality HAVING cnt > 100`
**并且** DuckDB 查询利用 Lance 谓词下推进行过滤操作
**并且** SQL 查询结果可通过 `query.to_arrow()` 转换为 Arrow RecordBatches
**并且** 两个已注册 Lance 表之间的 JOIN 操作在 Daft SQL 中正确工作
**并且** `tests/integration/test_sql_query.py` 针对测试 Lance 数据集验证 Daft SQL（主查询）和 DuckDB（目录回退）两种引擎

### Story 7.7: 自动分层 Blob 生命周期

作为一名平台运维人员，
我希望 S3 Lifecycle 规则根据可配置的年龄阈值自动将 Blob 数据从 Standard 过渡到 Infrequent Access 再到 Glacier 存储，
以便对不常访问的旧数据降低存储成本。

**验收标准：**

**假设** 有一个存储在 S3 上的 Lance 数据集，包含多保真度 Blob 存储（缩略图 + 预览 + 原始）
**当** 我配置生命周期规则：`standard_days=30`、`ia_days=90`、`glacier_days=365`
**那么** S3 Lifecycle 规则被应用：对象在 30 天后从 Standard 过渡到 IA，90 天后从 IA 过渡到 Glacier，365 天后保持 Glacier
**并且** 生命周期规则可通过目录元数据 API 按数据集配置
**并且** 缩略图和预览层级被排除在 Glacier 过渡之外（它们保留在 Standard 中以快速访问）
**并且** 仅原始保真度的 Blob 受生命周期过渡影响
**并且** 对于 100TB 数据集，估算的存储成本降低超过 50%，相比全 Standard 存储（NFR-COST-02）
**并且** 访问 Glacier 分层的对象触发恢复请求，支持可配置的 expedited/standard/bulk 检索
**并且** `tests/unit/test_blob_lifecycle.py` 验证生命周期规则生成和成本估算

### Story 7.8: Prometheus 指标端点

作为一名平台运维人员，
我希望有一个 Prometheus `/metrics` HTTP 端点，暴露所有平台指标，支持可配置的端口/路径和禁用功能，
以便 Prometheus 可以抓取系统、摄入、处理和查询指标用于可观测性。

**验收标准：**

**假设** Arrow Lake 平台正在运行，启用了可观测性
**当** 我向配置的指标端点发送 HTTP GET 请求（默认 `http://localhost:8000/metrics`）
**那么** 响应为 Prometheus 展示格式，包含所有功能 Epic 的指标（按 Epic 逐步引入），遵循命名模式 `arrow_lake_{domain}_{metric}_{unit}`
**并且** 摄入指标存在：`arrow_lake_ingestion_rows_total`、`arrow_lake_ingestion_bytes_total`、`arrow_lake_ingestion_duration_seconds`、`arrow_lake_ingestion_errors_total`（FR-OBS-02，在 Epic 3 引入）
**并且** 处理指标存在：`arrow_lake_processing_embeddings_total`、`arrow_lake_processing_quality_rejects_total`、`arrow_lake_processing_active_tasks`（FR-OBS-03，在 Epic 4 引入）
**并且** 查询指标存在：`arrow_lake_query_total`、`arrow_lake_query_latency_seconds`、`arrow_lake_query_results_total`，带 `query_type` 标签（FR-OBS-04，在 Epic 5 引入）
**并且** 系统指标存在：`arrow_lake_system_ray_actors`、`arrow_lake_system_tables`、`arrow_lake_system_uptime_seconds`（FR-OBS-05，在 Epic 6 引入）
**并且** 指标端口可通过 `ARROW_LAKE__METRICS_PORT` 配置，路径可通过 `ARROW_LAKE__METRICS_PATH` 配置（FR-OBS-06）
**并且** 设置 `ARROW_LAKE__METRICS_ENABLED=false` 完全禁用指标端点
**并且** `tests/unit/test_metrics_endpoint.py` 验证端口/路径配置、启用/禁用切换和响应格式

### Story 7.9: Grafana 仪表板

作为一名平台运维人员，
我希望有用于摄入、处理、查询性能和系统概览的预构建 Grafana 仪表板模板，
以便我可以实时监控平台健康状况，而无需从零开始构建仪表板。

**验收标准：**

**假设** Prometheus 已配置为抓取 Arrow Lake `/metrics` 端点
**当** 我从 `deploy/grafana/` 导入 Grafana 仪表板 JSON 模板
**那么** 摄入流水线仪表板显示：行/秒、字节/秒、错误率和按表的分布
**并且** 处理流水线仪表板显示：活跃任务、嵌入吞吐量、质量拒绝率
**并且** 查询性能仪表板显示：按类型（向量/文本/混合/SQL）的查询计数、p50/p95/p99 延迟
**并且** 系统概览仪表板显示：Ray Actor 数、已注册表、运行时间和资源利用率
**并且** 每个仪表板包含带适当时间范围和告警阈值的面板
**并且** 仪表板以可配置间隔自动刷新（默认 30 秒）
**并且** `deploy/grafana/` 包含：`ingestion-dashboard.json`、`processing-dashboard.json`、`query-dashboard.json`、`system-dashboard.json`

### Story 7.10: K8s Helm Chart 部署

作为一名 DevOps 工程师，
我希望有一个生产就绪的 Helm Chart，使用官方 Ray Helm Chart 和自定义 values 将 Arrow Lake 部署到 Kubernetes，
以便我可以使用标准 Helm 工作流进行部署、升级和回滚。

**验收标准：**

**假设** 有一个安装了 Helm 3 的 Kubernetes 集群
**当** 我运行 `helm install arrow-lake deploy/helm/arrow-lake -f deploy/helm/arrow-lake/values.yaml`
**那么** Chart 使用官方 Ray Helm Chart 作为依赖部署 Ray Head 服务、可配置的 Ray Worker 副本和关联资源
**并且** `deployment.yaml` 模板创建 Arrow Lake API 服务器部署，支持可配置副本和资源限制
**并且** `service.yaml` 模板将 API 服务器、Ray Dashboard 和指标端点暴露为 K8s Service
**并且** `networkpolicy.yaml` 模板定义网络策略，限制服务间通信（在 `values.yaml` 中默认禁用）
**并且** `prometheusrule.yaml` 模板配置关键指标的 Prometheus 告警规则
**并且** `values.yaml` 提供生产默认值，`values-dev.yaml` 提供开发覆盖
**并且** `helm upgrade arrow-lake` 执行滚动更新，无停机
**并且** `helm rollback arrow-lake 1` 成功回退到上一个版本
**并且** Chart 通过 `helm lint` 和 `helm template` dry-run 验证

### Story 7.11: Docker 网络隔离与安全

作为一名平台运维人员，
我希望有 Docker 网络隔离、TLS 加密、加密存储和限制的指标访问，
以便平台满足开发和生产环境的安全要求。

**验收标准：**

**假设** 有用于本地开发的 Docker Compose 配置
**当** 我运行 `docker compose up -d`
**那么** 所有服务通过专用 Docker bridge 网络通信，与主机网络隔离（AR-22）
**并且** 仅以下端口暴露到主机：8000（指标）、8265（Ray Dashboard）、9000（MinIO）、8888（Jupyter）
**并且** 为 Docker Compose 服务生成并配置自签名 TLS 证书（AR-23）
**并且** 为生产中的持久化存储配置启用了静态加密的 AWS GP3 EBS 卷（AR-24）
**并且** Prometheus 服务发现配置为仅从内部 Docker 网络内抓取 `/metrics`（AR-25）
**并且** 从 Docker 网络外部对指标端点的直接 HTTP 请求被拒绝
**并且** `tests/unit/test_security_config.py` 验证网络隔离、TLS 配置和指标访问控制

### Story 7.12: 单节点可扩展性与并发查询测试

作为一名性能工程师，
我希望有自动化的负载测试脚本，验证单节点数据量和并发查询吞吐量（NFR-SCALE-01、NFR-SCALE-03），
以便我可以在投资分布式基础设施之前确认平台在单机上的基线可扩展性要求。

**验收标准：**

**假设** 有一个已部署的 Arrow Lake 实例（单节点，CPU 或 GPU）
**当** 我运行 `pytest tests/benchmark/test_scale_single_node.py`
**那么** 单节点测试摄入 10M 行并验证查询检索延迟满足 NFR-SCALE-01（10M 行向量搜索 < 10ms）
**并且** 并发查询测试维持 100 QPS 并测量 P50/P95/P99 延迟（NFR-SCALE-03）
**并且** 所有基准测试结果作为结构化 JSON 记录在 `tests/benchmark/results/` 中
**并且** 结果包含：时间戳、集群配置、数据量、吞吐量、延迟百分位数
**并且** `tests/benchmark/test_scale_single_node.py` 可以在 CI 中运行（不需要 K8s 或多节点）

### Story 7.13: 分布式可扩展性与 GPU 突发测试

作为一名性能工程师，
我希望有分布式可扩展性和 GPU 突发负载测试，验证 NFR-SCALE-02、NFR-SCALE-04 和 NFR-SCALE-05，
以便我可以在支持 GPU 的 K8s 集群上确认平台满足生产级可扩展性要求。

**验收标准：**

**假设** 有一个部署了 Arrow Lake 并配置了 GPU 节点池的 K8s 集群
**当** 我运行 `pytest tests/benchmark/test_scale_distributed.py`
**那么** 分布式测试验证跨多节点的数据量支持达到 1B 行（NFR-SCALE-02）
**并且** 分数 GPU 测试以 0.5 GPU 增量配置 Worker 并验证正确分配（NFR-SCALE-04）
**并且** 弹性突发测试在 5 分钟内触发从 0 到 8 个 GPU Worker 的扩展（NFR-SCALE-05）
**并且** 分数 GPU 扩展需要 NVIDIA MIG 支持；如果 MIG 不可用，测试回退到整数 GPU 分配并记录警告
**并且** 这些测试从 CI 中排除，仅在专用 K8s 测试基础设施上运行（标记为 `@pytest.mark.distributed_gpu`）

### Story 7.14: CI/CD 流水线

作为一名开发者，
我希望在每次拉取请求时进行自动 CI 检查、夜间 GPU 测试运行和标签触发的发布工作流，
以便代码质量得到一致执行，发布可靠产生。

**验收标准：**

**假设** 有一个包含 Arrow Lake 代码库的 GitHub 仓库
**当** 我对主分支打开一个拉取请求
**那么** GitHub Actions CI 工作流运行：`ruff check .`（检查）、`mypy arrow_lake/`（类型检查）和 `pytest tests/unit/ tests/integration/`（仅 CPU 测试）（AR-47）
**并且** 如果任何检查未通过，CI 工作流失败，阻塞 PR 合并
**并且** 夜间工作流使用 `schedule: cron` 在计划时间触发 GPU 测试（AR-48）
**并且** 夜间 GPU 测试工作流也可以通过 `workflow_dispatch` 手动触发
**并且** 当推送匹配 `v*` 模式的 git 标签时，发布工作流构建并发布制品（AR-49）
**并且** 发布工作流将 Python 包发布到配置的注册表
**并且** 发布工作流从 conventional commit 消息生成变更日志
**并且** `tests/unit/test_ci_workflows.py` 验证所有三个工作流 YAML 文件正确解析并包含必需的作业步骤

---

## Epic 8: 高级功能

高级用户可以执行分面搜索、多模型集成搜索、数据血缘追踪、事件溯源审计和 NeMo Curator GPU 加速质量评分。

**FR：** FR-QRY-06、FR-QRY-08、FR-CAT-05、FR-ORCH-09、FR-PROC-04

**MVP：** 扩展（第 6-12 个月）

### Story 8.1: 带 DuckDB CUBE 的分面搜索

作为一名数据分析师，
我希望在向量搜索的同时进行多维分面导航，
以便我可以在保持相关性排名的同时，通过元数据分面（如模态、日期范围、质量分数和来源）缩小搜索结果范围。

**验收标准：**

**假设** 有一个已注册的 Lance 数据集，包含元数据列：`modality`、`source`、`quality_score`、`created_at` 和向量嵌入列
**当** 我执行分面搜索查询，过滤器为：`modality='image'`、`quality_score > 0.7` 和向量查询嵌入
**那么** DuckDB CUBE 从过滤后的数据集计算所有维度组合的分面计数
**并且** 分面计数与向量搜索结果在单个响应对象中返回
**并且** 分面响应包含：分面名称、分面值、计数和可选的子分面细分
**并且** 应用分面过滤器使用附加过滤器重新执行查询，而无需重新计算所有分面计数
**并且** 向量搜索结果与分面过滤条件正确相交，保持相关性排名
**并且** 分面搜索 API 可通过 `lake.search(query_vector, facets=["modality", "source", "quality_tier"])` 访问
**并且** `tests/integration/test_faceted_search.py` 验证分面计数正确性（针对具有已知数据的测试数据集）

### Story 8.2: 多模型集成搜索

作为一名机器学习工程师，
我希望组合多个嵌入模型的搜索结果，支持可配置的分数融合，
以便我可以利用不同嵌入模型的互补优势以获得更高的检索质量。

**验收标准：**

**假设** 有一个包含多个嵌入列的 Lance 数据集：`emb_text_768`（文本编码器）和 `emb_clip_512`（CLIP 视觉语言编码器）
**当** 我对两个嵌入列执行文本查询的集成搜索
**那么** 系统使用适当的索引分别对每个嵌入列执行向量搜索
**并且** 两个搜索的结果使用配置的融合策略合并
**并且** 分数融合支持三种模式：`average`（归一化分数的平均值）、`max`（每条结果的最佳分数）、`weighted`（每个模型的可配置权重）
**并且** 加权融合使用每个模型的可配置权重：`ensemble_weights={"emb_text_768": 0.6, "emb_clip_512": 0.4}`
**并且** 融合结果按行 ID 去重并按融合分数重新排列
**并且** top-k 结果返回每模型独立分数和融合分数
**并且** `tests/integration/test_ensemble_search.py` 验证 RRF 评分正确性、去重和结果排序

### Story 8.3: 通过 SQL 查询数据血缘

作为一名数据治理官，
我希望通过 SQL 查询任意数据集的完整数据血缘，
以便我可以追溯数据来源、应用了哪些转换以及哪些流水线产生了每个数据集版本。

**验收标准：**

**假设** 多个数据集已通过摄入和转换流水线创建，每个都有 Lance 版本历史
**当** 我查询血缘表：`SELECT * FROM lineage WHERE output_table = 'processed_images' ORDER BY timestamp DESC`
**那么** 结果包括：源数据集名称、转换类型（ingest/embed/quality/filter）、输出数据集名称、输出版本、流水线 run_id、操作者身份和时间戳
**并且** 血缘数据从 Lance 事件日志派生，以 SQL 可查询格式存储在目录中
**并且** 血缘查询支持按以下条件过滤：`output_table`、`source_table`、`transform_type`、`run_id`、`timestamp range`
**并且** 血缘查询支持与目录元数据的 JOIN，用 Schema 和统计信息丰富结果
**并且** 血缘链完整：任何输出数据集都可以追溯到所有中间步骤直到原始来源
**并且** Metaflow `run_id` 与血缘记录一起存储，用于流水线级关联
**并且** 血缘数据不可变：记录仅追加，从不修改或删除
**并且** `tests/integration/test_data_lineage.py` 创建多版本测试数据集并验证血缘 SQL 查询

### Story 8.4: 事件溯源审计追踪

作为一名合规审计师，
我希望有一个不可变的审计追踪，记录每次数据变更及其完整来源，
以便我可以验证数据完整性、重建任意历史状态并满足法规审计要求。

**验收标准：**

**假设** Arrow Lake 平台正在运行，启用了审计追踪
**当** 任何数据变更发生（ingest、update、delete、schema 变更、质量过滤、嵌入计算）
**那么** 记录一条不可变审计事件，包含：event_id（UUID）、timestamp、操作者身份、操作类型、受影响表、受影响版本（变更前/后）和流水线 run_id
**并且** 审计追踪实现为 Lance 版本变更日志 + Metaflow 标签 = 组合的不可变事件源（FR-ORCH-09）
**并且** 每条审计事件仅追加，创建后不可修改或删除
**并且** 任何表的完整历史可以通过按时间戳顺序重放审计事件来重建
**并且** 审计事件可通过 SQL 查询：`SELECT * FROM audit_log WHERE table_name = 'my_data' AND action = 'ingest' ORDER BY timestamp`
**并且** 每条审计事件包含 HMAC-SHA256 签名（使用服务端密钥），覆盖事件负载 + 前一事件的 HMAC — 提供防篡改检测，无需完整哈希链的复杂性
**并且** `tests/integration/test_event_sourcing.py` 创建测试流水线并验证审计事件创建、仅追加不可变性和 HMAC 链完整性

### Story 8.5: NeMo Curator GPU 质量评分流水线

作为一名准备大规模训练数据的数据工程师，
我希望使用 NeMo Curator 提供的 GPU 加速去重和质量评分，
以便我可以快速处理数百万样本，使用基于分类器的质量过滤器进行内容质量和语义去重。

**验收标准：**

**假设** 有一个包含需要质量评分的图像和文本列的 Lance 数据集
**当** 我运行启用 GPU 加速的 NeMo Curator 质量流水线
**那么** GPU 加速的精确去重使用 MinHash 和 LSH 识别并删除重复样本
**并且** 基于分类器的质量评分在 GPU 上运行：内容检测、美学质量评分和文本质量分类
**并且** 质量分数写入新的 Lance 列：`quality_nsfw_score`、`quality_aesthetic_score`、`quality_text_score` 和聚合的 `quality_composite_score`
**并且** cuDF 到 Arrow 的桥接在不产生 CPU 序列化瓶颈的情况下执行数据传输
**并且** 流水线在 GPU 上比仅 CPU 基线至少快 5 倍处理 100K 样本数据集
**并且** 当 GPU 不可用时，流水线自动回退到基于 CPU 的质量评分，使用基本启发式规则
**并且** CPU 回退产生兼容的质量分数列，使用相同的 Schema，允许透明切换
**并且** 质量评分结果与现有 QualityFilter 系统（FR-QUA-01）集成用于下游过滤
**并且** 被拒绝的样本（低于阈值）按 FR-QUA-03 路由到死信表
**并且** `tests/integration/test_nemo_curator.py` 使用模拟 GPU 验证去重正确性、分类阈值行为和 CPU 回退
