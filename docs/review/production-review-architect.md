# Arrow Lake v1.2 生产就绪性评估 — 架构师

**评估日期**: 2026-04-27
**评估范围**: 系统架构、可靠性韧性、可观测性、运维就绪性、性能基线

## 评估概要

| 维度 | 评级 | 状态 |
|------|------|------|
| 1. 系统架构 | **P1** | 建议发布前修复 |
| 2. 可靠性与韧性 | **P1** | 建议发布前修复 |
| 3. 可观测性 | **P2** | 可后续迭代 |
| 4. 运维就绪性 | **P2** | 可后续迭代 |
| 5. 性能基线 | **P1** | 建议发布前修复 |

**综合评级: P1 — 可在修复关键项后发布**

---

## 1. 系统架构 — P1

### 1.1 模块边界与耦合度

**优点:**
- Mixin 模式 (`_LakeIngestMixin`, `_LakeSearchMixin`, `_LakeQueryMixin` 等) 将功能域做了清晰的水平切分
- `Lake` 类通过 `_get_component(key, factory)` 实现延迟初始化和组件缓存
- 查询层与存储层通过依赖注入解耦
- 配置系统 (`ArrowLakeConfig`) 将 28 个子配置模块化

**问题:**
- **P1-ARCH-1**: Mixin 之间通过 `self._config`、`self._storage`、`self._components` 直接访问共享状态，缺乏显式接口约束。建议引入 Protocol 接口。
- **P1-ARCH-2**: `_get_component` 返回 `Any` 类型，丢失类型安全。应引入泛型或 Protocol。
- **P2-ARCH-3**: `LanceStorageManager` 超过 900 行，承担过多职责。建议拆分。

### 1.2 扩展性

**优点:**
- 存储后端通过 `StorageBackend` 枚举支持 LOCAL/MINIO/S3/GCS 切换
- LLM Provider 通过 `BaseLLMProvider` 抽象支持多种后端
- Embedding encoder 本地/API 双路径自动降级

**问题:**
- **P2-ARCH-4**: 新增查询后端需在 `_LakeSearchMixin` 中硬编码，缺乏插件注册机制
- **P2-ARCH-5**: `Ingestor` 对文件格式通过 `_SUPPORTED_EXTENSIONS` 硬编码

### 1.3 依赖管理

**问题:**
- **P1-ARCH-6**: `torch>=2.4` 作为核心依赖（~2GB），在许多场景下仅用于 GPU 检测。建议移至可选依赖组。

### 1.4 配置系统

**PASS — 已达标:** 四层覆盖系统（代码默认值 → .env → 环境变量 → YAML），YAML 深度合并。

- **P2-CONFIG-1**: 缺少配置热更新能力

---

## 2. 可靠性与韧性 — P1

### 2.1 错误处理策略

**优点:**
- 异常体系完善：`ArrowLakeError` 基类 + 17 子类 + 90+ ErrorCode
- 外部调用使用 `tenacity` 重试（指数退避 + 最大 3 次）
- ApiEmbeddingEncoder 具有本地模型降级路径

**问题:**
- **P1-REL-1**: 缺少熔断器 (circuit breaker)。外部依赖持续失败时系统持续重试不熔断
- **P1-REL-2**: `Ingestor._write_table` 非原子性，中途失败无回滚
- **P2-REL-3**: RAG 流式 SSE 中途断开无法区分正常结束和异常中断

### 2.2 资源管理

**优点:**
- `DuckDBSessionManager` 设计成熟：信号量并发控制、空闲回收、慢查询检测、Prometheus 指标

**问题:**
- **P1-REL-4**: `Lake.shutdown()` 仅关闭 session_manager，不关闭 LLM Provider、KG client 等资源
- **P2-REL-5**: `LocalEmbeddingEncoder._fallback_cache` 类级别缓存可能锁住 GPU 显存

### 2.3 优雅关闭

- **P1-REL-6**: FastAPI lifespan shutdown 阶段不完整，`auth_service` 处理为 `pass`

### 2.4 数据一致性

**优点:**
- Backup 原子写入（staging → copy → delete）+ SHA-256 校验
- Lance 版本控制提供时间旅行能力

**问题:**
- **P1-REL-7**: 并发写入同一数据集缺乏锁保护，TOCTOU 竞态
- **P2-REL-8**: 备份大文件整体读入内存可能 OOM

---

## 3. 可观测性 — P2

### 3.1 日志

**优点:** structlog + JSON 渲染，correlation_id 注入

**问题:**
- **P2-OBS-1**: 混用 structlog 和标准 logging
- **P2-OBS-2**: 缺少 request_id、user_id 等业务上下文

### 3.2 指标

**优点:** 完备的 Prometheus 指标体系（arrow_lake_ 前缀），20+ 指标，6 个 Grafana dashboard

**问题:**
- **P2-OBS-3**: `http_request_duration_seconds` path label 可能高基数
- **P2-OBS-4**: 缺少业务级 SLI 指标

### 3.3 链路追踪

**优点:** OpenTelemetry 集成，TraceIdRatioBased 采样

**问题:**
- **P2-OBS-5**: OTel 仅 instrument FastAPI 层，内部调用链缺少 span
- **P2-OBS-6**: 缺少 metric → trace 关联

### 3.4 告警规则

**优点:** 10 条 PrometheusRule 告警规则

**问题:**
- **P2-OBS-7**: `prometheusRules.enabled` 默认 false
- **P2-OBS-8**: 缺少 DuckDB 连接池耗尽告警

---

## 4. 运维就绪性 — P2

### 4.1 健康检查

**PASS — 已达标:** 三级健康检查（live/ready/compat），Helm probe 配置正确

### 4.2 部署配置

**优点:** 多阶段 Docker 构建，非 root 用户，Helm Chart 完整

**问题:**
- **P2-OPS-1**: 默认单副本，多副本限制未文档化
- **P2-OPS-2**: 缺少 PDB/HPA 模板
- **P2-OPS-3**: Docker Compose 缺少 Alertmanager
- **P2-OPS-4**: NetworkPolicy 默认 false

### 4.3 备份恢复

**优点:** BackupManager 功能完整，原子 manifest，SHA-256 校验

**问题:**
- **P2-OPS-5**: 缺少自动备份调度
- **P2-OPS-6**: 缺少 dry-run 模式

### 4.4 文档

**优点:** README、SECURITY、CONTRIBUTING、CHANGELOG 齐全

**问题:**
- **P2-OPS-7**: 缺少生产运维手册（runbook）

---

## 5. 性能基线 — P1

### 5.1 查询性能

**优点:** DuckDB native + LanceDB SDK 双路径，流式扫描，LIMIT 上推，索引自动调优

**问题:**
- **P1-PERF-1**: `max_concurrent_queries=4` 可能成为瓶颈
- **P1-PERF-2**: 混合搜索并行结果合并内存峰值翻倍

### 5.2 并发能力

**问题:**
- **P1-PERF-3**: 速率限制纯内存实现，多实例不共享
- **P2-PERF-4**: uvicorn 单 worker，CPU 密集操作阻塞事件循环

### 5.3 资源使用

**问题:**
- **P2-PERF-5**: CUDA 内存碎片化风险
- **P2-PERF-6**: DuckDB 临时文件无监控限制

---

## 优先修复建议

### 发布前必须修复 (P1)

| 编号 | 问题 | 估时 |
|------|------|------|
| P1-REL-4 | shutdown 不完整，需关闭全部资源链 | 2h |
| P1-REL-6 | FastAPI lifespan shutdown 空操作 | 1h |
| P1-REL-7 | 并发写入 TOCTOU 竞态 | 3h |
| P1-REL-1 | 缺少熔断器 | 4h |
| P1-ARCH-6 | torch 核心依赖过大 | 1h |
| P1-PERF-1 | DuckDB 并发瓶颈文档化 | 2h |

### 后续迭代 (P2)

| 编号 | 问题 |
|------|------|
| P2-ARCH-3 | LanceStorageManager 拆分 |
| P2-OBS-5 | OTel 内部 span 覆盖 |
| P2-OPS-2 | Helm HPA/PDB 模板 |
| P2-CONFIG-1 | 配置热更新 |
